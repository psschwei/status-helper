"""Tests for read-side queries against the local cache.

Persist known rows directly, then assert the query shapes them correctly. ``list_repositories``
in particular must report accurate per-repo counts (including a repo with zero children) and
a stable order — without an N+1 query per repository.
"""

from datetime import timedelta

from sqlmodel import Session

from status_assistant.models import ActivityKind
from status_assistant.queries import (
    get_engineer_view,
    get_whats_happened,
    list_engineers,
    list_repositories,
)
from tests.conftest import (
    FIXED_TIME,
    make_activity_event,
    make_issue,
    make_issue_assignee,
    make_link,
    make_pull_request,
    make_repository,
    make_review_request,
)


def test_list_repositories_reports_counts_and_order(session: Session) -> None:
    # Two repos: one with 2 PRs + 1 issue, one with nothing.
    zebra = make_repository(id=1, owner="z", name="zebra", full_name="z/zebra")
    apple = make_repository(id=2, owner="a", name="apple", full_name="a/apple")
    session.add(zebra)
    session.add(apple)
    session.add(make_pull_request(101, 1, "PR one", repository_id=1))
    session.add(make_pull_request(102, 2, "PR two", repository_id=1))
    session.add(make_issue(201, 3, "Issue one", repository_id=1))
    session.commit()

    items = list_repositories(session)

    # Ordered by full_name: "a/apple" before "z/zebra".
    assert [i.repository.full_name for i in items] == ["a/apple", "z/zebra"]

    by_name = {i.repository.full_name: i for i in items}
    assert (by_name["z/zebra"].pull_request_count, by_name["z/zebra"].issue_count) == (2, 1)
    # A repo with no children reports zero, not a missing row.
    assert (by_name["a/apple"].pull_request_count, by_name["a/apple"].issue_count) == (0, 0)


def test_list_repositories_empty(session: Session) -> None:
    assert list_repositories(session) == []


# --- Engineers -------------------------------------------------------------------


def test_list_engineers_counts_prs_by_author_and_issues_by_assignee(session: Session) -> None:
    session.add(make_repository(id=1, full_name="a/one"))
    # PRs counted by author: alice 2, bob 1.
    session.add(make_pull_request(101, 1, "P1", repository_id=1, author_login="alice"))
    session.add(make_pull_request(102, 2, "P2", repository_id=1, author_login="alice"))
    session.add(make_pull_request(103, 3, "P3", repository_id=1, author_login="bob"))
    # Issues counted by *assignee*, not author. Author is "carol" for both (make_issue's
    # default) but that must not give carol any credit.
    session.add(make_issue(201, 4, "I1", repository_id=1))
    session.add(make_issue(202, 5, "I2", repository_id=1))
    # I1 assigned to bob; I2 assigned to both alice and dave.
    session.add(make_issue_assignee(201, "bob"))
    session.add(make_issue_assignee(202, "alice"))
    session.add(make_issue_assignee(202, "dave"))
    session.commit()

    items = list_engineers(session)

    # Union of PR authors and issue assignees, ordered by login. carol (author only) absent;
    # dave (assignee only) present.
    assert [i.login for i in items] == ["alice", "bob", "dave"]
    by_login = {i.login: i for i in items}
    assert (by_login["alice"].pull_request_count, by_login["alice"].issue_count) == (2, 1)
    assert (by_login["bob"].pull_request_count, by_login["bob"].issue_count) == (1, 1)
    # dave has no PRs, just the one co-assigned issue.
    assert (by_login["dave"].pull_request_count, by_login["dave"].issue_count) == (0, 1)


def test_list_engineers_issue_assigned_to_two_counts_for_both(session: Session) -> None:
    session.add(make_repository(id=1, full_name="a/one"))
    session.add(make_issue(201, 1, "Shared", repository_id=1))
    session.add(make_issue_assignee(201, "alice"))
    session.add(make_issue_assignee(201, "bob"))
    session.commit()

    by_login = {i.login: i for i in list_engineers(session)}

    # The same issue is counted once for each assignee.
    assert by_login["alice"].issue_count == 1
    assert by_login["bob"].issue_count == 1


def test_list_engineers_excludes_missing_logins(session: Session) -> None:
    session.add(make_repository(id=1, full_name="a/one"))
    session.add(make_pull_request(101, 1, "P1", repository_id=1, author_login=None))
    session.add(make_pull_request(102, 2, "P2", repository_id=1, author_login=""))
    session.add(make_pull_request(103, 3, "P3", repository_id=1, author_login="alice"))
    session.commit()

    items = list_engineers(session)

    assert [i.login for i in items] == ["alice"]


def test_list_engineers_empty(session: Session) -> None:
    assert list_engineers(session) == []


def test_list_engineers_filters_to_allowed_logins(session: Session) -> None:
    session.add(make_repository(id=1, full_name="a/one"))
    session.add(make_pull_request(101, 1, "P1", repository_id=1, author_login="alice"))
    session.add(make_pull_request(102, 2, "P2", repository_id=1, author_login="bob"))
    session.add(make_issue(201, 3, "I1", repository_id=1))
    session.add(make_issue_assignee(201, "carol"))
    session.commit()

    # Only alice is in the roster; bob (PR) and carol (assignee) are dropped. A roster handle
    # with no work ("dave") simply doesn't appear.
    items = list_engineers(session, allowed_logins={"alice", "dave"})

    assert [i.login for i in items] == ["alice"]


def test_list_engineers_none_filter_shows_everyone(session: Session) -> None:
    session.add(make_repository(id=1, full_name="a/one"))
    session.add(make_pull_request(101, 1, "P1", repository_id=1, author_login="alice"))
    session.add(make_pull_request(102, 2, "P2", repository_id=1, author_login="bob"))
    session.commit()

    assert [i.login for i in list_engineers(session, allowed_logins=None)] == ["alice", "bob"]


def test_get_engineer_view_excluded_login_is_none(session: Session) -> None:
    session.add(make_repository(id=1, full_name="a/one"))
    session.add(make_pull_request(101, 1, "P1", repository_id=1, author_login="bob"))
    session.commit()

    # bob has open work, but is not in the roster, so the per-engineer view is unreachable.
    assert get_engineer_view(session, "bob", allowed_logins={"alice"}) is None
    # ...and in the roster, it resolves normally.
    view = get_engineer_view(session, "bob", allowed_logins={"bob"})
    assert view is not None and view.login == "bob"


def test_get_engineer_view_groups_work_per_repo(session: Session) -> None:
    session.add(make_repository(id=1, owner="z", name="zebra", full_name="z/zebra"))
    session.add(make_repository(id=2, owner="a", name="apple", full_name="a/apple"))
    # alice: a PR in zebra and an issue *assigned* to her in apple; bob's work is excluded.
    session.add(make_pull_request(101, 1, "Z PR", repository_id=1, author_login="alice"))
    session.add(make_issue(201, 2, "A issue", repository_id=2))
    session.add(make_issue_assignee(201, "alice"))
    session.add(make_pull_request(102, 3, "B PR", repository_id=1, author_login="bob"))
    session.commit()

    view = get_engineer_view(session, "alice")

    assert view is not None
    assert view.login == "alice"
    assert (view.pull_request_count, view.issue_count) == (1, 1)
    # Repos ordered by full_name: "a/apple" before "z/zebra".
    assert [r.repository.full_name for r in view.repos] == ["a/apple", "z/zebra"]
    apple, zebra = view.repos
    # No links, so everything lands in the unpaired sections.
    assert [i.number for i in apple.issues_without_pr] == [2]
    assert apple.prs_without_issue == []
    assert [pr.number for pr in zebra.prs_without_issue] == [1]
    assert zebra.issues_without_pr == []


def test_get_engineer_view_lists_assigned_not_authored_issues(session: Session) -> None:
    session.add(make_repository(id=1, full_name="a/one"))
    # alice authored this issue but it's assigned to bob — it belongs to bob's view, not hers.
    session.add(make_issue(201, 1, "Bug", repository_id=1, author_login="alice"))
    session.add(make_issue_assignee(201, "bob"))
    session.commit()

    assert get_engineer_view(session, "alice") is None
    bob = get_engineer_view(session, "bob")
    assert bob is not None
    assert [i.number for i in bob.repos[0].issues_without_pr] == [1]


def test_get_engineer_view_orders_items_by_updated_desc(session: Session) -> None:
    session.add(make_repository(id=1, full_name="a/one"))
    older = FIXED_TIME
    newer = FIXED_TIME + timedelta(days=1)
    session.add(
        make_pull_request(101, 1, "old", repository_id=1, author_login="alice", updated_at=older)
    )
    session.add(
        make_pull_request(102, 2, "new", repository_id=1, author_login="alice", updated_at=newer)
    )
    session.commit()

    view = get_engineer_view(session, "alice")

    assert view is not None
    assert [pr.number for pr in view.repos[0].prs_without_issue] == [2, 1]


def test_get_engineer_view_pairs_linked_issue_and_pr(session: Session) -> None:
    """A PR linked to an open, cached issue shows as a pair — not in the unpaired lists."""
    session.add(make_repository(id=1, full_name="a/one"))
    # alice's PR closes an issue assigned to her, plus a standalone PR and a standalone issue.
    session.add(make_pull_request(101, 1, "Fix bug", repository_id=1, author_login="alice"))
    session.add(make_issue(201, 2, "The bug", repository_id=1))
    session.add(make_issue_assignee(201, "alice"))
    session.add(make_link(101, 201))
    session.add(make_pull_request(102, 3, "Lone PR", repository_id=1, author_login="alice"))
    session.add(make_issue(202, 4, "Lone issue", repository_id=1))
    session.add(make_issue_assignee(202, "alice"))
    session.commit()

    view = get_engineer_view(session, "alice")

    assert view is not None
    work = view.repos[0]
    assert [(p.issue.number, p.pull_request.number) for p in work.paired] == [(2, 1)]
    assert [i.number for i in work.issues_without_pr] == [4]
    assert [pr.number for pr in work.prs_without_issue] == [3]
    # Distinct counts: the paired issue/PR aren't double-counted.
    assert (view.pull_request_count, view.issue_count) == (2, 2)


def test_get_engineer_view_pairs_via_union_attribution(session: Session) -> None:
    """A pair surfaces when the engineer owns *either* side — not only both."""
    session.add(make_repository(id=1, full_name="a/one"))
    # alice authored the PR; the linked issue is assigned to *carol*, not alice.
    session.add(make_pull_request(101, 1, "Fix", repository_id=1, author_login="alice"))
    session.add(make_issue(201, 2, "Bug", repository_id=1))
    session.add(make_issue_assignee(201, "carol"))
    session.add(make_link(101, 201))
    session.commit()

    view = get_engineer_view(session, "alice")

    assert view is not None
    work = view.repos[0]
    # alice's PR pulls in carol's issue as the paired counterpart.
    assert [(p.issue.number, p.pull_request.number) for p in work.paired] == [(2, 1)]
    assert work.issues_without_pr == []
    assert work.prs_without_issue == []


def test_get_engineer_view_ignores_link_to_uncached_issue(session: Session) -> None:
    """A link whose issue isn't in the view leaves the PR in the unpaired section.

    Ingestion only stores links to cached issues, but a link can dangle if an issue was
    concurrently removed; the query must not crash or fabricate a pair.
    """
    session.add(make_repository(id=1, full_name="a/one"))
    session.add(make_pull_request(101, 1, "Fix", repository_id=1, author_login="alice"))
    session.add(make_link(101, 999))  # issue 999 doesn't exist
    session.commit()

    view = get_engineer_view(session, "alice")

    assert view is not None
    work = view.repos[0]
    assert work.paired == []
    assert [pr.number for pr in work.prs_without_issue] == [1]


def test_get_engineer_view_unknown_login_is_none(session: Session) -> None:
    session.add(make_repository(id=1, full_name="a/one"))
    session.add(make_pull_request(101, 1, "P1", repository_id=1, author_login="alice"))
    session.commit()

    assert get_engineer_view(session, "nobody") is None


# --- Reviews ---------------------------------------------------------------------


def test_get_engineer_view_lists_reviews_owed(session: Session) -> None:
    """A PR where the engineer is a requested reviewer shows under reviews_owed."""
    session.add(make_repository(id=1, owner="a", name="one", full_name="a/one"))
    # bob authored a PR and requested alice to review it.
    session.add(make_pull_request(101, 1, "Bob's PR", repository_id=1, author_login="bob"))
    session.add(make_review_request(101, "alice"))
    # alice also has her own PR (not a review she owes).
    session.add(make_pull_request(102, 2, "Alice's PR", repository_id=1, author_login="alice"))
    session.commit()

    view = get_engineer_view(session, "alice")

    assert view is not None
    assert [r.pull_request.number for r in view.reviews_owed] == [1]
    owed = view.reviews_owed[0]
    assert owed.repository.full_name == "a/one"
    assert owed.requested_reviewers == ["alice"]


def test_get_engineer_view_reviews_owed_excludes_own_pr(session: Session) -> None:
    """You don't owe a review on your own PR, even if somehow listed as a reviewer."""
    session.add(make_repository(id=1, full_name="a/one"))
    session.add(make_pull_request(101, 1, "Alice's PR", repository_id=1, author_login="alice"))
    session.add(make_review_request(101, "alice"))
    session.commit()

    view = get_engineer_view(session, "alice")

    assert view is not None
    assert view.reviews_owed == []


def test_get_engineer_view_lists_prs_awaiting_review(session: Session) -> None:
    """The engineer's own PR with a pending reviewer shows under prs_awaiting_review."""
    session.add(make_repository(id=1, full_name="a/one"))
    # alice's PR awaits bob and carol; her other PR has no reviewer requested.
    session.add(make_pull_request(101, 1, "Needs review", repository_id=1, author_login="alice"))
    session.add(make_review_request(101, "bob"))
    session.add(make_review_request(101, "carol"))
    session.add(make_pull_request(102, 2, "No reviewer", repository_id=1, author_login="alice"))
    session.commit()

    view = get_engineer_view(session, "alice")

    assert view is not None
    assert [r.pull_request.number for r in view.prs_awaiting_review] == [1]
    # The full outstanding-reviewer set is attached, sorted.
    assert view.prs_awaiting_review[0].requested_reviewers == ["bob", "carol"]


def test_get_engineer_view_only_reviews_owed_still_returns_view(session: Session) -> None:
    """An engineer with no PRs/issues of their own, but a review to do, still gets a page."""
    session.add(make_repository(id=1, full_name="a/one"))
    session.add(make_pull_request(101, 1, "Bob's PR", repository_id=1, author_login="bob"))
    session.add(make_review_request(101, "alice"))
    session.commit()

    view = get_engineer_view(session, "alice")

    assert view is not None
    assert view.login == "alice"
    assert (view.pull_request_count, view.issue_count) == (0, 0)
    assert [r.pull_request.number for r in view.reviews_owed] == [1]
    # The reviewed PR is bob's, so it isn't in alice's own per-repo work.
    assert view.repos == []


def test_get_engineer_view_orders_reviews_owed_by_updated_desc(session: Session) -> None:
    session.add(make_repository(id=1, full_name="a/one"))
    older = FIXED_TIME
    newer = FIXED_TIME + timedelta(days=1)
    session.add(
        make_pull_request(101, 1, "old", repository_id=1, author_login="bob", updated_at=older)
    )
    session.add(
        make_pull_request(102, 2, "new", repository_id=1, author_login="bob", updated_at=newer)
    )
    session.add(make_review_request(101, "alice"))
    session.add(make_review_request(102, "alice"))
    session.commit()

    view = get_engineer_view(session, "alice")

    assert view is not None
    assert [r.pull_request.number for r in view.reviews_owed] == [2, 1]


# --- get_whats_happened -----------------------------------------------------------


def test_get_whats_happened_groups_by_engineer(session: Session) -> None:
    session.add(make_repository())
    # bob has two actions, alice one — grouped per person, engineers ordered by login.
    session.add(make_activity_event(ActivityKind.PR_MERGED, 1, actor_login="bob"))
    session.add(make_activity_event(ActivityKind.PR_OPENED, 2, actor_login="alice"))
    session.add(make_activity_event(ActivityKind.ISSUE_CLOSED, 3, actor_login="bob"))
    session.commit()

    view = get_whats_happened(session, FIXED_TIME - timedelta(hours=1))

    assert [eng.login for eng in view.engineers] == ["alice", "bob"]
    counts = {eng.login: eng.action_count for eng in view.engineers}
    assert counts == {"alice": 1, "bob": 2}
    # The repository rides along on each row, joined without a per-event query.
    assert view.engineers[0].prs[0].repository.full_name == "octocat/hello-world"


def test_get_whats_happened_splits_prs_reviews_issues(session: Session) -> None:
    """Under an engineer, actions are split into PR / review / issue sections."""
    session.add(make_repository())
    session.add(make_activity_event(ActivityKind.PR_MERGED, 1, actor_login="alice"))
    session.add(make_activity_event(ActivityKind.PR_OPENED, 2, actor_login="alice"))
    session.add(
        make_activity_event(
            ActivityKind.REVIEW_SUBMITTED, 3, detail="approved", detail_id=1, actor_login="alice"
        )
    )
    session.add(make_activity_event(ActivityKind.ISSUE_CLOSED, 4, actor_login="alice"))
    session.commit()

    (alice,) = get_whats_happened(session, FIXED_TIME - timedelta(hours=1)).engineers
    assert {a.action_phrase for a in alice.prs} == {"merged PR #1", "opened PR #2"}
    assert [a.action_phrase for a in alice.reviews] == ["reviewed PR #3"]
    assert [a.action_phrase for a in alice.issues] == ["closed issue #4"]
    # Header count is the total across all three sections.
    assert alice.action_count == 4


def test_get_whats_happened_dedupes_repeated_action_on_same_subject(session: Session) -> None:
    """Several reviews on the same PR (mixed verdicts) collapse to one "reviewed" row."""
    session.add(make_repository())
    # Different verdicts, but all render as "reviewed" — so they dedupe together.
    for i, (verdict, hours) in enumerate(
        (("commented", 1), ("approved", 2), ("changes_requested", 3))
    ):
        session.add(
            make_activity_event(
                ActivityKind.REVIEW_SUBMITTED, 7, detail=verdict, detail_id=i,
                occurred_at=FIXED_TIME + timedelta(hours=hours),
            )
        )
    session.commit()

    view = get_whats_happened(session, FIXED_TIME - timedelta(hours=1))
    (alice,) = view.engineers
    (row,) = alice.reviews  # collapsed to a single row in the Reviews section
    assert row.action_phrase == "reviewed PR #7"
    assert row.count == 3
    # Most recent of the three. SQLite drops tzinfo on read, so compare the wall-clock value.
    assert row.latest.replace(tzinfo=None) == (FIXED_TIME + timedelta(hours=3)).replace(
        tzinfo=None
    )


def test_get_whats_happened_keeps_distinct_phrases_on_same_subject(session: Session) -> None:
    """Different verbs on one subject are NOT merged: opened and merged PR #5 stay separate."""
    session.add(make_repository())
    session.add(
        make_activity_event(ActivityKind.PR_OPENED, 5, occurred_at=FIXED_TIME + timedelta(hours=1))
    )
    session.add(
        make_activity_event(ActivityKind.PR_MERGED, 5, occurred_at=FIXED_TIME + timedelta(hours=2))
    )
    session.commit()

    view = get_whats_happened(session, FIXED_TIME - timedelta(hours=1))
    (alice,) = view.engineers
    # Two rows in the PRs section, newest-first by latest date; neither is a count>1 merge.
    assert [a.action_phrase for a in alice.prs] == ["merged PR #5", "opened PR #5"]
    assert all(a.count == 1 for a in alice.prs)


def test_get_whats_happened_rows_within_section_are_newest_first(session: Session) -> None:
    session.add(make_repository())
    session.add(
        make_activity_event(ActivityKind.PR_OPENED, 1, occurred_at=FIXED_TIME + timedelta(hours=1))
    )
    session.add(
        make_activity_event(ActivityKind.PR_MERGED, 2, occurred_at=FIXED_TIME + timedelta(hours=3))
    )
    session.add(
        make_activity_event(ActivityKind.PR_CLOSED, 3, occurred_at=FIXED_TIME + timedelta(hours=2))
    )
    session.commit()

    view = get_whats_happened(session, FIXED_TIME - timedelta(hours=1))
    (alice,) = view.engineers
    # All three are PRs → one section, newest-first by date.
    assert [a.subject_title for a in alice.prs] == ["Subject #2", "Subject #3", "Subject #1"]


def test_get_whats_happened_null_actor_bucket_sorts_last(session: Session) -> None:
    session.add(make_repository())
    session.add(make_activity_event(ActivityKind.PR_OPENED, 1, actor_login="alice"))
    # A null-actor event (a ghost account) becomes the login=None bucket, ordered last.
    session.add(make_activity_event(ActivityKind.ISSUE_CLOSED, 2, actor_login=None))
    session.commit()

    view = get_whats_happened(session, FIXED_TIME - timedelta(hours=1))
    assert [eng.login for eng in view.engineers] == ["alice", None]


def test_get_whats_happened_boundary_is_exclusive(session: Session) -> None:
    session.add(make_repository())
    session.add(make_activity_event(ActivityKind.PR_MERGED, 1, occurred_at=FIXED_TIME))
    session.commit()

    # An event exactly at `since` is not "since the scrum" — excluded (no engineers at all).
    assert get_whats_happened(session, FIXED_TIME).engineers == []
    # A hair before, it's included.
    assert len(get_whats_happened(session, FIXED_TIME - timedelta(seconds=1)).engineers) == 1


def test_get_whats_happened_respects_roster(session: Session) -> None:
    session.add(make_repository())
    session.add(make_activity_event(ActivityKind.PR_OPENED, 1, actor_login="alice"))
    session.add(make_activity_event(ActivityKind.PR_OPENED, 2, actor_login="bob"))
    # A null-actor event (e.g. a ghost account) — dropped under any roster filter.
    session.add(make_activity_event(ActivityKind.ISSUE_CLOSED, 3, actor_login=None))
    session.commit()

    since = FIXED_TIME - timedelta(hours=1)
    # With a roster, only alice's bucket survives (bob filtered, null dropped).
    filtered = get_whats_happened(session, since, allowed_logins={"alice"})
    assert [eng.login for eng in filtered.engineers] == ["alice"]
    # With no roster (None), everyone including the null-actor bucket is shown.
    unfiltered = get_whats_happened(session, since, allowed_logins=None)
    assert [eng.login for eng in unfiltered.engineers] == ["alice", "bob", None]


def test_get_whats_happened_empty(session: Session) -> None:
    view = get_whats_happened(session, FIXED_TIME)
    assert view.engineers == []
    assert view.since == FIXED_TIME


def test_get_whats_happened_action_phrases(session: Session) -> None:
    session.add(make_repository())
    session.add(
        make_activity_event(
            ActivityKind.REVIEW_SUBMITTED, 42, detail="approved", detail_id=7,
            occurred_at=FIXED_TIME + timedelta(hours=1),
        )
    )
    session.add(
        make_activity_event(
            ActivityKind.PR_MERGED, 9, occurred_at=FIXED_TIME + timedelta(hours=2)
        )
    )
    session.commit()

    view = get_whats_happened(session, FIXED_TIME)
    (alice,) = view.engineers  # both default to actor "alice"
    assert [a.action_phrase for a in alice.reviews] == ["reviewed PR #42"]  # verdict collapsed
    assert [a.action_phrase for a in alice.prs] == ["merged PR #9"]
