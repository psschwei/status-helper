"""Tests for read-side queries against the local cache.

Persist known rows directly, then assert the query shapes them correctly. ``list_repositories``
in particular must report accurate per-repo counts (including a repo with zero children) and
a stable order — without an N+1 query per repository.
"""

from datetime import timedelta

from sqlmodel import Session

from status_assistant.queries import (
    get_engineer_view,
    list_engineers,
    list_repositories,
)
from tests.conftest import FIXED_TIME, make_issue, make_pull_request, make_repository


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


def test_list_engineers_counts_across_prs_and_issues(session: Session) -> None:
    session.add(make_repository(id=1, full_name="a/one"))
    # alice: 2 PRs; bob: 1 PR + 1 issue; carol: 1 issue only.
    session.add(make_pull_request(101, 1, "P1", repository_id=1, author_login="alice"))
    session.add(make_pull_request(102, 2, "P2", repository_id=1, author_login="alice"))
    session.add(make_pull_request(103, 3, "P3", repository_id=1, author_login="bob"))
    session.add(make_issue(201, 4, "I1", repository_id=1, author_login="bob"))
    session.add(make_issue(202, 5, "I2", repository_id=1, author_login="carol"))
    session.commit()

    items = list_engineers(session)

    # Ordered by login, union across both tables.
    assert [i.login for i in items] == ["alice", "bob", "carol"]
    by_login = {i.login: i for i in items}
    assert (by_login["alice"].pull_request_count, by_login["alice"].issue_count) == (2, 0)
    assert (by_login["bob"].pull_request_count, by_login["bob"].issue_count) == (1, 1)
    assert (by_login["carol"].pull_request_count, by_login["carol"].issue_count) == (0, 1)


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


def test_get_engineer_view_groups_work_per_repo(session: Session) -> None:
    session.add(make_repository(id=1, owner="z", name="zebra", full_name="z/zebra"))
    session.add(make_repository(id=2, owner="a", name="apple", full_name="a/apple"))
    # alice authored work in both repos, plus an issue; bob's work must be excluded.
    session.add(make_pull_request(101, 1, "Z PR", repository_id=1, author_login="alice"))
    session.add(make_issue(201, 2, "A issue", repository_id=2, author_login="alice"))
    session.add(make_pull_request(102, 3, "B PR", repository_id=1, author_login="bob"))
    session.commit()

    view = get_engineer_view(session, "alice")

    assert view is not None
    assert view.login == "alice"
    assert (view.pull_request_count, view.issue_count) == (1, 1)
    # Repos ordered by full_name: "a/apple" before "z/zebra".
    assert [r.repository.full_name for r in view.repos] == ["a/apple", "z/zebra"]
    apple, zebra = view.repos
    assert [i.number for i in apple.issues] == [2]
    assert apple.pull_requests == []
    assert [pr.number for pr in zebra.pull_requests] == [1]


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
    assert [pr.number for pr in view.repos[0].pull_requests] == [2, 1]


def test_get_engineer_view_unknown_login_is_none(session: Session) -> None:
    session.add(make_repository(id=1, full_name="a/one"))
    session.add(make_pull_request(101, 1, "P1", repository_id=1, author_login="alice"))
    session.commit()

    assert get_engineer_view(session, "nobody") is None
