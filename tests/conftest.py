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

from status_assistant.ai.base import SummaryPrompt  # noqa: E402
from status_assistant.config import get_settings  # noqa: E402
from status_assistant.connectors.base import (  # noqa: E402
    IssueWithAssignees,
    PullRequestWithReviewers,
)
from status_assistant.db import get_session  # noqa: E402
from status_assistant.dependencies import get_connector, get_optional_summarizer  # noqa: E402
from status_assistant.engineers_config import EngineerRef  # noqa: E402
from status_assistant.main import app  # noqa: E402
from status_assistant.models import (  # noqa: E402
    Issue,
    IssueAssignee,
    PRReviewRequest,
    PullRequest,
    PullRequestIssueLink,
    Repository,
)
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
        assignees: dict[int, list[str]] | None = None,
        reviewers: dict[int, list[str]] | None = None,
        links: list[tuple[int, int]] | None = None,
    ) -> None:
        self._repository = repository
        self._pull_requests = pull_requests or []
        self._issues = issues or []
        # Canned assignee logins keyed by issue id. Kept parallel to ``issues`` (rather than
        # on the Issue objects) because assignees ride *alongside* the issue in the real
        # connector too — and a private attr on Issue wouldn't survive model_dump() below.
        self._assignees = assignees or {}
        # Canned requested-reviewer logins keyed by PR id — parallel to ``pull_requests``, for
        # the same reason assignees are kept off the model.
        self._reviewers = reviewers or {}
        # Canned (pull_request_id, issue_id) closing links, mirroring the real connector's
        # ``list_closing_issue_links``. Unfiltered — ingestion drops links to un-cached issues.
        self._links = links or []

    # Rebuild fresh instances from field values on every call. A real connector returns new
    # objects each time; more importantly, ``model_copy()`` on a SQLModel *table* instance
    # produces an object SQLAlchemy can't insert (instrumented attributes don't round-trip),
    # so we reconstruct from ``model_dump()`` instead.
    def get_repository(self, owner: str, name: str) -> Repository:
        return Repository(**self._repository.model_dump())

    def list_pull_requests(
        self, owner: str, name: str, *, state: str = "open"
    ) -> list[PullRequestWithReviewers]:
        return [
            PullRequestWithReviewers(
                pull_request=PullRequest(**pr.model_dump()),
                requested_reviewer_logins=list(self._reviewers.get(pr.id, [])),
            )
            for pr in self._pull_requests
        ]

    def list_issues(
        self, owner: str, name: str, *, state: str = "open"
    ) -> list[IssueWithAssignees]:
        return [
            IssueWithAssignees(
                issue=Issue(**issue.model_dump()),
                assignee_logins=list(self._assignees.get(issue.id, [])),
            )
            for issue in self._issues
        ]

    def list_closing_issue_links(self, owner: str, name: str) -> list[tuple[int, int]]:
        return list(self._links)


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
    ) -> list[PullRequestWithReviewers]:
        return self._for(owner, name).list_pull_requests(owner, name, state=state)

    def list_issues(
        self, owner: str, name: str, *, state: str = "open"
    ) -> list[IssueWithAssignees]:
        return self._for(owner, name).list_issues(owner, name, state=state)

    def list_closing_issue_links(self, owner: str, name: str) -> list[tuple[int, int]]:
        return self._for(owner, name).list_closing_issue_links(owner, name)


class FakeSummarizer:
    """An ``AISummarizer`` returning canned, inspectable text — no LLM call.

    Records the last prompt it was handed (so a test can assert the facts were passed) and
    returns a string that echoes a marker plus the user message, so the persisted summary is
    both deterministic and traceable back to the input.
    """

    MARKER = "SUMMARY::"

    def __init__(self) -> None:
        self.last_prompt: SummaryPrompt | None = None

    def summarize(self, prompt: SummaryPrompt) -> str:
        self.last_prompt = prompt
        return f"{self.MARKER}{prompt.user}"


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


def make_issue_assignee(issue_id: int, login: str) -> IssueAssignee:
    return IssueAssignee(issue_id=issue_id, login=login)


def make_link(pull_request_id: int, issue_id: int) -> PullRequestIssueLink:
    return PullRequestIssueLink(pull_request_id=pull_request_id, issue_id=issue_id)


def make_review_request(pull_request_id: int, login: str) -> PRReviewRequest:
    return PRReviewRequest(pull_request_id=pull_request_id, login=login)


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
def no_summarizer(client: TestClient) -> None:
    """Force the "LLM not configured" path regardless of the machine's ``.env``.

    ``get_optional_summarizer`` takes ``settings`` as a plain default arg (not a ``Depends``),
    so FastAPI resolves it with ``settings=None`` and it falls back to the *real* ``Settings``
    — the ``get_settings`` stub doesn't reach it. On a dev machine with a real ``LLM_API_KEY``
    that yields a live summarizer, so a test asserting the 503/not-configured path must pin the
    dependency to ``None`` explicitly (mirroring how ``use_summarizer`` pins it to a fake)."""
    app.dependency_overrides[get_optional_summarizer] = lambda: None


@pytest.fixture
def use_summarizer(client: TestClient) -> Callable[[FakeSummarizer], None]:
    """Return a helper that installs a fake summarizer for the client.

    Overrides ``get_optional_summarizer`` (what the routes depend on) so the LLM is never
    called. Without this override the dependency returns ``None`` — the "not configured"
    path — since tests set no ``LLM_API_KEY``.
    """

    def _install(summarizer: FakeSummarizer) -> None:
        app.dependency_overrides[get_optional_summarizer] = lambda: summarizer
        # Keep the settings flag consistent: a summarizer being available means the UI should
        # offer the generate button. This installs/mutates the same stub as use_repos/use_engineers.
        _stub_settings().llm_configured = True

    return _install


class _StubSettings:
    """A stand-in for ``Settings`` carrying just the config the routes read.

    Shared by ``use_repos`` and ``use_engineers`` so a test can install both without one
    clobbering the other's ``get_settings`` override. Defaults are empty: no repos, and an
    empty roster (which means "show everyone" — no filter).
    """

    def __init__(self) -> None:
        self.repos: list[RepoRef] = []
        self.engineers: list[EngineerRef] = []
        # AI-summary config the engineer routes read. Default: LLM not configured (no key),
        # so the "not configured" path is exercised unless a test installs a fake summarizer
        # (which overrides get_optional_summarizer directly, independent of this flag).
        self.llm_configured: bool = False
        self.llm_model: str = "test-model"

    def load_repos(self) -> list[RepoRef]:
        return list(self.repos)

    def load_engineers(self) -> list[EngineerRef]:
        return list(self.engineers)

    def __call__(self) -> "_StubSettings":
        # FastAPI resolves a dependency override by calling it; a stub instance returns
        # itself so a single mutable stub backs the whole request.
        return self


def _stub_settings() -> _StubSettings:
    """Return the installed stub settings, installing one on first use.

    Idempotent so ``use_repos`` and ``use_engineers`` compose: both mutate the same stub.
    """
    override = app.dependency_overrides.get(get_settings)
    if isinstance(override, _StubSettings):
        return override
    stub = _StubSettings()
    app.dependency_overrides[get_settings] = stub
    return stub


@pytest.fixture
def use_repos(client: TestClient) -> Callable[[list[tuple[str, str]]], None]:
    """Return a helper that sets the configured (repos.toml) repositories for the client.

    Overrides ``get_settings`` with a stub whose ``load_repos`` yields the given
    ``(owner, name)`` pairs, so dashboard and sync-all routes see a controlled repo list
    without reading a real ``.env`` or ``repos.toml``.
    """

    def _install(pairs: list[tuple[str, str]]) -> None:
        _stub_settings().repos = [RepoRef(owner=o, name=n) for o, n in pairs]

    return _install


@pytest.fixture
def use_engineers(client: TestClient) -> Callable[[list[EngineerRef]], None]:
    """Return a helper that sets the engineer roster (engineers.toml) for the client.

    Overrides ``get_settings`` with a stub whose ``load_engineers`` yields the given roster,
    so the Engineers routes apply the filter without reading a real ``engineers.toml``.
    """

    def _install(engineers: list[EngineerRef]) -> None:
        _stub_settings().engineers = list(engineers)

    return _install
