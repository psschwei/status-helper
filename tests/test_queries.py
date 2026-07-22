"""Tests for read-side queries against the local cache.

Persist known rows directly, then assert the query shapes them correctly. ``list_repositories``
in particular must report accurate per-repo counts (including a repo with zero children) and
a stable order — without an N+1 query per repository.
"""

from sqlmodel import Session

from status_assistant.queries import list_repositories
from tests.conftest import make_issue, make_pull_request, make_repository


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
