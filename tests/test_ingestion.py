"""Tests for repository ingestion — the cache-consistency guarantees.

Uses ``FakeGitHubConnector`` (canned domain objects) against an in-memory database. Proves
that re-syncing is idempotent and that the stored set always matches "currently open" on
GitHub: retitled items update in place, and items that have closed drop out.
"""

from sqlmodel import Session, select

from status_assistant.ingestion.sync import sync_repository
from status_assistant.models import Issue, PullRequest
from tests.conftest import (
    FakeGitHubConnector,
    make_issue,
    make_pull_request,
    make_repository,
)


def test_sync_persists_repository_prs_and_issues(session: Session) -> None:
    connector = FakeGitHubConnector(
        repository=make_repository(),
        pull_requests=[make_pull_request(101, 1, "Add X"), make_pull_request(102, 2, "Add Y")],
        issues=[make_issue(201, 3, "A bug")],
    )

    result = sync_repository(session, connector, "octocat", "hello-world")

    assert result.pull_requests == 2
    assert result.issues == 1
    assert result.repository_id == 1296269
    assert result.last_synced_at is not None

    prs = session.exec(select(PullRequest)).all()
    issues = session.exec(select(Issue)).all()
    assert len(prs) == 2
    assert len(issues) == 1
    # Ingestion stamps the real repository id onto the children (connector sends 0).
    assert {pr.repository_id for pr in prs} == {1296269}


def test_resync_is_idempotent_and_reflects_current_state(session: Session) -> None:
    repo = make_repository()

    # First sync: PRs 101 & 102, issue 201.
    sync_repository(
        session,
        FakeGitHubConnector(
            repository=repo,
            pull_requests=[make_pull_request(101, 1, "Add X"), make_pull_request(102, 2, "Add Y")],
            issues=[make_issue(201, 3, "A bug")],
        ),
        "octocat",
        "hello-world",
    )

    # Second sync: PR 101 has closed (gone), 102 retitled, 103 is new; the issue closed.
    sync_repository(
        session,
        FakeGitHubConnector(
            repository=repo,
            pull_requests=[
                make_pull_request(102, 2, "Add Y (renamed)"),
                make_pull_request(103, 4, "Add Z"),
            ],
            issues=[],
        ),
        "octocat",
        "hello-world",
    )

    prs = {pr.id: pr for pr in session.exec(select(PullRequest)).all()}
    issues = session.exec(select(Issue)).all()

    # No duplication; state converges to what the second sync reported.
    assert set(prs) == {102, 103}
    assert prs[102].title == "Add Y (renamed)"
    assert 101 not in prs  # closed PR dropped from the active cache
    assert issues == []  # closed issue dropped


def test_resync_updates_last_synced_at(session: Session) -> None:
    connector = FakeGitHubConnector(repository=make_repository())

    first = sync_repository(session, connector, "octocat", "hello-world")
    second = sync_repository(session, connector, "octocat", "hello-world")

    assert second.last_synced_at >= first.last_synced_at
