"""Shared test fixtures.

Everything here keeps tests hermetic: an in-memory SQLite database, a fake connector that
implements the ``GitHubConnector`` protocol (so no test touches GitHub), and a ``TestClient``
with the app's session and connector dependencies overridden to use them.
"""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from status_assistant.db import get_session
from status_assistant.dependencies import get_connector
from status_assistant.main import app
from status_assistant.models import Issue, PullRequest, Repository

FIXED_TIME = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


@pytest.fixture
def engine() -> Iterator[Engine]:
    """A fresh in-memory SQLite database per test.

    ``StaticPool`` + a shared connection keeps the same in-memory DB across sessions within
    one test (otherwise each connection would get its own empty database).
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


class FakeGitHubConnector:
    """A ``GitHubConnector`` backed by in-memory canned data — no network.

    Used for ingestion and API tests that don't care about githubkit specifics. Connector
    *mapping* is tested separately in ``test_connector.py`` against a mocked githubkit client.
    """

    def __init__(
        self,
        *,
        repository: Repository,
        pull_requests: list[PullRequest] | None = None,
        issues: list[Issue] | None = None,
    ) -> None:
        self._repository = repository
        self._pull_requests = pull_requests or []
        self._issues = issues or []

    # Rebuild fresh instances from field values on every call. A real connector returns new
    # objects each time; more importantly, ``model_copy()`` on a SQLModel *table* instance
    # produces an object SQLAlchemy can't insert (instrumented attributes don't round-trip),
    # so we reconstruct from ``model_dump()`` instead.
    def get_repository(self, owner: str, name: str) -> Repository:
        return Repository(**self._repository.model_dump())

    def list_pull_requests(
        self, owner: str, name: str, *, state: str = "open"
    ) -> list[PullRequest]:
        return [PullRequest(**pr.model_dump()) for pr in self._pull_requests]

    def list_issues(self, owner: str, name: str, *, state: str = "open") -> list[Issue]:
        return [Issue(**issue.model_dump()) for issue in self._issues]


def make_repository(**overrides: object) -> Repository:
    defaults: dict[str, object] = dict(
        id=1296269,
        owner="octocat",
        name="hello-world",
        full_name="octocat/hello-world",
        github_base_url="https://api.github.com",
        html_url="https://github.com/octocat/hello-world",
    )
    defaults.update(overrides)
    return Repository(**defaults)


def make_pull_request(pr_id: int, number: int, title: str, **overrides: object) -> PullRequest:
    defaults: dict[str, object] = dict(
        id=pr_id,
        number=number,
        repository_id=0,
        title=title,
        state="open",
        is_draft=False,
        author_login="alice",
        html_url=f"https://github.com/octocat/hello-world/pull/{number}",
        created_at=FIXED_TIME,
        updated_at=FIXED_TIME,
    )
    defaults.update(overrides)
    return PullRequest(**defaults)


def make_issue(issue_id: int, number: int, title: str, **overrides: object) -> Issue:
    defaults: dict[str, object] = dict(
        id=issue_id,
        number=number,
        repository_id=0,
        title=title,
        state="open",
        author_login="carol",
        html_url=f"https://github.com/octocat/hello-world/issues/{number}",
        created_at=FIXED_TIME,
        updated_at=FIXED_TIME,
    )
    defaults.update(overrides)
    return Issue(**defaults)


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    """A ``TestClient`` whose DB session is bound to the in-memory ``engine``.

    The connector dependency is left to individual tests to override (via
    ``app.dependency_overrides[get_connector]``), since different tests want different data.
    """

    def override_get_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def use_connector(client: TestClient) -> Callable[[FakeGitHubConnector], None]:
    """Return a helper that installs a ``FakeGitHubConnector`` for the client."""

    def _install(connector: FakeGitHubConnector) -> None:
        app.dependency_overrides[get_connector] = lambda: connector

    return _install
