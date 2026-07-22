"""Shared test fixtures.

Everything here keeps tests hermetic: an in-memory SQLite database, a fake connector that
implements the ``GitHubConnector`` protocol (so no test touches GitHub), and a ``TestClient``
with the app's session and connector dependencies overridden to use them.
"""

import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

# Provide the settings the app needs *before* status_assistant.config is imported, so tests
# are hermetic: ``get_settings()`` (called directly by the app's lifespan, not via a
# dependency, so it can't be overridden) constructs successfully without a real ``.env`` or
# token. The database is overridden per-test to an in-memory engine; this URL is never used.
os.environ.setdefault("GITHUB_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import Engine  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402
from sqlmodel.pool import StaticPool  # noqa: E402

from status_assistant.config import get_settings  # noqa: E402
from status_assistant.db import get_session  # noqa: E402
from status_assistant.dependencies import get_connector  # noqa: E402
from status_assistant.main import app  # noqa: E402
from status_assistant.models import Issue, PullRequest, Repository  # noqa: E402
from status_assistant.repos_config import RepoRef  # noqa: E402

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


class FakeMultiRepoConnector:
    """A ``GitHubConnector`` serving different canned data per ``owner/name``.

    ``sync_all`` calls one connector once per configured repo, so unlike
    ``FakeGitHubConnector`` (which returns the same repo for any owner/name) this looks the
    request up by key. Reconstructs fresh instances per call for the same reason.
    """

    def __init__(self, repos: dict[tuple[str, str], "FakeGitHubConnector"]) -> None:
        self._repos = repos

    def _for(self, owner: str, name: str) -> "FakeGitHubConnector":
        return self._repos[(owner, name)]

    def get_repository(self, owner: str, name: str) -> Repository:
        return self._for(owner, name).get_repository(owner, name)

    def list_pull_requests(
        self, owner: str, name: str, *, state: str = "open"
    ) -> list[PullRequest]:
        return self._for(owner, name).list_pull_requests(owner, name, state=state)

    def list_issues(self, owner: str, name: str, *, state: str = "open") -> list[Issue]:
        return self._for(owner, name).list_issues(owner, name, state=state)


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
def use_connector(
    client: TestClient,
) -> Callable[[FakeGitHubConnector | FakeMultiRepoConnector], None]:
    """Return a helper that installs a fake connector for the client."""

    def _install(connector: FakeGitHubConnector | FakeMultiRepoConnector) -> None:
        app.dependency_overrides[get_connector] = lambda: connector

    return _install


@pytest.fixture
def use_repos(client: TestClient) -> Callable[[list[tuple[str, str]]], None]:
    """Return a helper that sets the configured (repos.toml) repositories for the client.

    Overrides ``get_settings`` with a stub whose ``load_repos`` yields the given
    ``(owner, name)`` pairs, so dashboard and sync-all routes see a controlled repo list
    without reading a real ``.env`` or ``repos.toml``.
    """

    def _install(pairs: list[tuple[str, str]]) -> None:
        refs = [RepoRef(owner=o, name=n) for o, n in pairs]

        class _StubSettings:
            def load_repos(self) -> list[RepoRef]:
                return list(refs)

        app.dependency_overrides[get_settings] = _StubSettings

    return _install
