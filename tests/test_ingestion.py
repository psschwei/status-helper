"""Tests for repository ingestion — the cache-consistency guarantees.

Uses ``FakeGitHubConnector`` (canned domain objects) against an in-memory database. Proves
that re-syncing is idempotent and that the stored set always matches "currently open" on
GitHub: retitled items update in place, and items that have closed drop out.
"""

from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from status_assistant.ingestion.sync import (
    prune_activity_events,
    sync_all,
    sync_repository,
)
from status_assistant.models import (
    ActivityEvent,
    ActivityKind,
    ClosingIssueLink,
    Issue,
    IssueAssignee,
    PRReviewRequest,
    PullRequest,
    PullRequestIssueLink,
    Repository,
)
from status_assistant.repos_config import RepoRef
from tests.conftest import (
    RECENT_TIME,
    FakeGitHubConnector,
    FakeMultiRepoConnector,
    make_activity_event,
    make_activity_record,
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


def test_sync_persists_issue_assignees(session: Session) -> None:
    connector = FakeGitHubConnector(
        repository=make_repository(),
        issues=[make_issue(201, 3, "A bug"), make_issue(202, 4, "Unassigned")],
        # Issue 201 has two assignees; issue 202 has none.
        assignees={201: ["dave", "erin"]},
    )

    sync_repository(session, connector, "octocat", "hello-world")

    rows = session.exec(select(IssueAssignee)).all()
    assert {(r.issue_id, r.login) for r in rows} == {(201, "dave"), (201, "erin")}
    # The unassigned issue produces no assignee rows.
    assert all(r.issue_id != 202 for r in rows)


def test_resync_replaces_issue_assignees(session: Session) -> None:
    repo = make_repository()

    sync_repository(
        session,
        FakeGitHubConnector(
            repository=repo,
            issues=[make_issue(201, 3, "A bug")],
            assignees={201: ["dave", "erin"]},
        ),
        "octocat",
        "hello-world",
    )
    # Second sync: assignment changed to just frank; dave/erin must not linger.
    sync_repository(
        session,
        FakeGitHubConnector(
            repository=repo,
            issues=[make_issue(201, 3, "A bug")],
            assignees={201: ["frank"]},
        ),
        "octocat",
        "hello-world",
    )

    rows = session.exec(select(IssueAssignee)).all()
    assert {(r.issue_id, r.login) for r in rows} == {(201, "frank")}


def test_sync_persists_pr_review_requests(session: Session) -> None:
    connector = FakeGitHubConnector(
        repository=make_repository(),
        pull_requests=[make_pull_request(101, 1, "Add X"), make_pull_request(102, 2, "Add Y")],
        # PR 101 has two requested reviewers; PR 102 has none.
        reviewers={101: ["dave", "erin"]},
    )

    sync_repository(session, connector, "octocat", "hello-world")

    rows = session.exec(select(PRReviewRequest)).all()
    assert {(r.pull_request_id, r.login) for r in rows} == {(101, "dave"), (101, "erin")}
    # The PR with no requested reviewers produces no review-request rows.
    assert all(r.pull_request_id != 102 for r in rows)


def test_resync_replaces_pr_review_requests(session: Session) -> None:
    repo = make_repository()

    sync_repository(
        session,
        FakeGitHubConnector(
            repository=repo,
            pull_requests=[make_pull_request(101, 1, "Add X")],
            reviewers={101: ["dave", "erin"]},
        ),
        "octocat",
        "hello-world",
    )
    # Second sync: the review request changed to just frank; dave/erin must not linger.
    sync_repository(
        session,
        FakeGitHubConnector(
            repository=repo,
            pull_requests=[make_pull_request(101, 1, "Add X")],
            reviewers={101: ["frank"]},
        ),
        "octocat",
        "hello-world",
    )

    rows = session.exec(select(PRReviewRequest)).all()
    assert {(r.pull_request_id, r.login) for r in rows} == {(101, "frank")}


def test_sync_persists_pr_issue_links_only_for_cached_issues(session: Session) -> None:
    """A closing link is stored only when *both* endpoints are in the fetched open set."""
    connector = FakeGitHubConnector(
        repository=make_repository(),
        pull_requests=[make_pull_request(101, 1, "Fix"), make_pull_request(102, 2, "Other")],
        issues=[make_issue(201, 3, "A bug")],
        # 101→201 is fully cached; 102→999 references a closed/absent issue and must be dropped.
        links=[(101, 201), (102, 999)],
    )

    sync_repository(session, connector, "octocat", "hello-world")

    rows = session.exec(select(PullRequestIssueLink)).all()
    assert {(r.pull_request_id, r.issue_id) for r in rows} == {(101, 201)}


def test_resync_replaces_pr_issue_links(session: Session) -> None:
    repo = make_repository()

    sync_repository(
        session,
        FakeGitHubConnector(
            repository=repo,
            pull_requests=[make_pull_request(101, 1, "Fix")],
            issues=[make_issue(201, 3, "A bug")],
            links=[(101, 201)],
        ),
        "octocat",
        "hello-world",
    )
    # Second sync: the PR now closes a different issue; the old link must not linger.
    sync_repository(
        session,
        FakeGitHubConnector(
            repository=repo,
            pull_requests=[make_pull_request(101, 1, "Fix")],
            issues=[make_issue(202, 4, "Another bug")],
            links=[(101, 202)],
        ),
        "octocat",
        "hello-world",
    )

    rows = session.exec(select(PullRequestIssueLink)).all()
    assert {(r.pull_request_id, r.issue_id) for r in rows} == {(101, 202)}


def test_sync_persists_closing_issue_number_links(session: Session) -> None:
    """Number-keyed closing links are persisted for every pair, un-gated by the open set."""
    connector = FakeGitHubConnector(
        repository=make_repository(),
        pull_requests=[make_pull_request(101, 1, "Fix")],
        issues=[make_issue(201, 3, "A bug")],
        # The issue (number 7) need NOT be in the open set — the durable link is stored anyway,
        # since it must outlive the issue's closing.
        number_links=[(1, 7)],
    )

    sync_repository(session, connector, "octocat", "hello-world")

    rows = session.exec(select(ClosingIssueLink)).all()
    assert {(r.repository_id, r.pr_number, r.issue_number) for r in rows} == {(1296269, 1, 7)}


def test_resync_keeps_closing_issue_number_links_append_only(session: Session) -> None:
    """Unlike PullRequestIssueLink, closing number-links are never deleted on re-sync."""
    repo = make_repository()
    sync_repository(
        session,
        FakeGitHubConnector(repository=repo, number_links=[(1, 7)]),
        "octocat",
        "hello-world",
    )
    # A later sync no longer reports the link (the PR merged and left the open set), but the
    # durable row must remain — and re-observing it must not duplicate it.
    sync_repository(
        session,
        FakeGitHubConnector(repository=repo, number_links=[(1, 7), (2, 8)]),
        "octocat",
        "hello-world",
    )

    rows = session.exec(select(ClosingIssueLink)).all()
    assert {(r.pr_number, r.issue_number) for r in rows} == {(1, 7), (2, 8)}


def test_resync_updates_last_synced_at(session: Session) -> None:
    connector = FakeGitHubConnector(repository=make_repository())

    first = sync_repository(session, connector, "octocat", "hello-world")
    second = sync_repository(session, connector, "octocat", "hello-world")

    assert second.last_synced_at >= first.last_synced_at


def test_sync_all_syncs_every_configured_repo(session: Session) -> None:
    repo_a = make_repository(id=1, owner="octocat", name="hello-world",
                             full_name="octocat/hello-world")
    repo_b = make_repository(id=2, owner="acme", name="api", full_name="acme/api")
    connector = FakeMultiRepoConnector(
        {
            ("octocat", "hello-world"): FakeGitHubConnector(
                repository=repo_a,
                pull_requests=[make_pull_request(101, 1, "Add X")],
                issues=[make_issue(201, 2, "A bug")],
            ),
            ("acme", "api"): FakeGitHubConnector(
                repository=repo_b,
                pull_requests=[
                    make_pull_request(102, 1, "Add Y"),
                    make_pull_request(103, 2, "Add Z"),
                ],
            ),
        }
    )
    repos = [RepoRef(owner="octocat", name="hello-world"), RepoRef(owner="acme", name="api")]

    results = sync_all(session, connector, repos)

    # One result per configured repo, each with its own counts.
    assert {r.full_name: (r.pull_requests, r.issues) for r in results} == {
        "octocat/hello-world": (1, 1),
        "acme/api": (2, 0),
    }
    # Both repositories and all their children persisted under the right repo ids.
    assert {r.id for r in session.exec(select(Repository)).all()} == {1, 2}
    assert {pr.repository_id for pr in session.exec(select(PullRequest)).all()} == {1, 2}


# --- Activity events (the append-only history) ----------------------------------------


def test_sync_persists_activity_events(session: Session) -> None:
    connector = FakeGitHubConnector(
        repository=make_repository(),
        activity=[
            make_activity_record("pr_merged", 42, subject_title="Add X"),
            make_activity_record("issue_closed", 7, subject_title="A bug"),
            make_activity_record(
                "review_submitted", 42, actor_login="bob", detail="approved", review_id=999
            ),
        ],
    )

    result = sync_repository(session, connector, "octocat", "hello-world")

    assert result.events == 3
    events = {e.id: e for e in session.exec(select(ActivityEvent)).all()}
    # Deterministic keys: state transitions carry no detail id; reviews carry the review id.
    assert set(events) == {
        "1296269:pr:42:pr_merged",
        "1296269:issue:7:issue_closed",
        "1296269:pr:42:review_submitted:999",
    }
    merged = events["1296269:pr:42:pr_merged"]
    assert merged.subject_title == "Add X"
    # Ingestion stamps the real repo id (the connector doesn't know it).
    assert merged.repository_id == 1296269
    assert events["1296269:pr:42:review_submitted:999"].detail == "approved"


def test_resync_appends_activity_and_never_deletes(session: Session) -> None:
    """The durability guarantee: unlike the open snapshot, past events survive a re-sync."""
    repo = make_repository()

    # First sync: PR 101 is open, and there's a merge event for the (now-gone) PR 100.
    sync_repository(
        session,
        FakeGitHubConnector(
            repository=repo,
            pull_requests=[make_pull_request(101, 1, "Open work")],
            activity=[make_activity_record("pr_merged", 100, subject_title="Old PR")],
        ),
        "octocat",
        "hello-world",
    )

    # Second sync: PR 101 has since closed (drops from the snapshot), and a *new*, disjoint
    # event arrives. The connector no longer reports the old merge event at all.
    sync_repository(
        session,
        FakeGitHubConnector(
            repository=repo,
            pull_requests=[],
            activity=[make_activity_record("issue_closed", 5, subject_title="New issue")],
        ),
        "octocat",
        "hello-world",
    )

    # Snapshot converged to "currently open" — PR 101 is gone.
    assert session.exec(select(PullRequest)).all() == []
    # But BOTH events remain: the append-only log never deletes, even the one the second sync
    # didn't report.
    assert {e.id for e in session.exec(select(ActivityEvent)).all()} == {
        "1296269:pr:100:pr_merged",
        "1296269:issue:5:issue_closed",
    }


def test_resync_activity_is_idempotent(session: Session) -> None:
    repo = make_repository()
    # Two reviews by the SAME actor on the SAME PR — distinct only by review id. They must stay
    # two rows, not collapse into one.
    activity = [
        make_activity_record("pr_opened", 42),
        make_activity_record(
            "review_submitted", 42, actor_login="bob", detail="commented", review_id=1
        ),
        make_activity_record(
            "review_submitted", 42, actor_login="bob", detail="approved", review_id=2
        ),
    ]

    sync_repository(
        session,
        FakeGitHubConnector(repository=repo, activity=activity),
        "octocat",
        "hello-world",
    )
    # Re-syncing the same window must not duplicate (merge upserts by the deterministic key).
    sync_repository(
        session,
        FakeGitHubConnector(repository=repo, activity=activity),
        "octocat",
        "hello-world",
    )

    ids = {e.id for e in session.exec(select(ActivityEvent)).all()}
    assert ids == {
        "1296269:pr:42:pr_opened",
        "1296269:pr:42:review_submitted:1",
        "1296269:pr:42:review_submitted:2",
    }


def test_sync_activity_respects_lookback_window(session: Session) -> None:
    """The fake filters by ``since``, so an event older than the lookback isn't fetched."""
    recent = make_activity_record("pr_merged", 42, occurred_at=RECENT_TIME)
    ancient = make_activity_record(
        "pr_merged", 1, occurred_at=datetime.now(UTC) - timedelta(days=400)
    )
    connector = FakeGitHubConnector(
        repository=make_repository(), activity=[recent, ancient]
    )

    # sync uses now - 14 days as the bound; RECENT_TIME (yesterday) is within it, the
    # 400-day-old event is not.
    result = sync_repository(session, connector, "octocat", "hello-world")

    assert result.events == 1
    assert {e.subject_number for e in session.exec(select(ActivityEvent)).all()} == {42}


# --- Activity retention (the one place events are deleted) -------------------------


def test_prune_activity_events_deletes_only_old(session: Session) -> None:
    now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    session.add(make_repository())
    # One event just inside the 28-day window, one just outside it.
    session.add(
        make_activity_event(
            ActivityKind.PR_MERGED, 1, occurred_at=now - timedelta(days=27)
        )
    )
    session.add(
        make_activity_event(
            ActivityKind.PR_MERGED, 2, occurred_at=now - timedelta(days=29)
        )
    )
    session.commit()

    removed = prune_activity_events(session, now=now)

    assert removed == 1
    surviving = {e.subject_number for e in session.exec(select(ActivityEvent)).all()}
    assert surviving == {1}  # the 27-day-old event is kept; the 29-day-old one is gone


def test_prune_activity_events_boundary_is_inclusive_of_survivors(session: Session) -> None:
    now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    session.add(make_repository())
    # Exactly at the cutoff (occurred_at == now - retention) is NOT older-than, so it survives.
    session.add(
        make_activity_event(
            ActivityKind.PR_MERGED, 1, occurred_at=now - timedelta(days=28)
        )
    )
    session.commit()

    assert prune_activity_events(session, now=now) == 0
    assert len(session.exec(select(ActivityEvent)).all()) == 1


def test_prune_activity_events_spans_all_repos(session: Session) -> None:
    now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    session.add(make_repository(id=1, full_name="a/one"))
    session.add(make_repository(id=2, full_name="b/two"))
    old = now - timedelta(days=40)
    session.add(make_activity_event(ActivityKind.PR_MERGED, 1, repository_id=1, occurred_at=old))
    session.add(make_activity_event(ActivityKind.PR_MERGED, 2, repository_id=2, occurred_at=old))
    session.commit()

    # A single global sweep clears old events across every repository.
    assert prune_activity_events(session, now=now) == 2
    assert session.exec(select(ActivityEvent)).all() == []


def test_prune_sweeps_stale_closing_issue_links(session: Session) -> None:
    """The retention sweep also drops ClosingIssueLink rows older than the cutoff."""
    now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    session.add(make_repository())
    session.add(
        ClosingIssueLink(
            repository_id=1296269, pr_number=1, issue_number=7,
            observed_at=now - timedelta(days=27),  # inside the window → kept
        )
    )
    session.add(
        ClosingIssueLink(
            repository_id=1296269, pr_number=2, issue_number=8,
            observed_at=now - timedelta(days=29),  # outside the window → swept
        )
    )
    session.commit()

    prune_activity_events(session, now=now)

    surviving = {r.pr_number for r in session.exec(select(ClosingIssueLink)).all()}
    assert surviving == {1}


def test_sync_all_prunes_old_activity(session: Session) -> None:
    repo = make_repository()
    # Seed a stale event directly, then run sync_all — which prunes after syncing.
    session.add(repo)
    session.add(
        make_activity_event(
            ActivityKind.PR_MERGED, 99, occurred_at=datetime.now(UTC) - timedelta(days=60)
        )
    )
    session.commit()

    sync_all(
        session,
        FakeGitHubConnector(
            repository=make_repository(),
            activity=[make_activity_record("pr_merged", 1)],  # a fresh (recent) event
        ),
        [RepoRef(owner="octocat", name="hello-world")],
    )

    numbers = {e.subject_number for e in session.exec(select(ActivityEvent)).all()}
    assert 99 not in numbers  # the 60-day-old event was purged
    assert 1 in numbers  # the freshly-synced event remains


def test_prune_keeps_everything_the_fetch_window_would_refetch(session: Session) -> None:
    """Retention must exceed the fetch lookback, so a purge never deletes a re-fetchable event."""
    from status_assistant.ingestion.sync import _ACTIVITY_LOOKBACK, _ACTIVITY_RETENTION

    assert _ACTIVITY_RETENTION > _ACTIVITY_LOOKBACK
