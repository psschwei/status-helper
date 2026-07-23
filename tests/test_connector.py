"""Tests for the githubkit-backed connector's mapping logic.

The githubkit ``GitHub`` client is mocked at the object boundary, so these tests exercise
*our* vendor-to-domain mapping (field selection, the draft flag, ghost-user handling, and the
PR-in-issues filter) without any network or a real token — and without depending on
githubkit's large, strict response schemas.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from status_assistant.connectors.github import GitHubKitConnector

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def ns(**kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


class _FakeRest:
    class repos:
        @staticmethod
        def get(owner: str, repo: str) -> SimpleNamespace:
            return ns(
                parsed_data=ns(
                    id=1296269,
                    full_name=f"{owner}/{repo}",
                    html_url=f"https://github.com/{owner}/{repo}",
                )
            )

    class pulls:
        @staticmethod
        def list(**kwargs: Any) -> None:  # referenced by identity in paginate()
            ...

        @staticmethod
        def list_reviews(**kwargs: Any) -> None:  # referenced by identity in paginate()
            ...

    class issues:
        @staticmethod
        def list_for_repo(**kwargs: Any) -> None:
            ...


class _FakeGitHub:
    """Stands in for githubkit.GitHub, recording construction args for assertions."""

    last_kwargs: dict[str, Any] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _FakeGitHub.last_kwargs = {"args": args, "kwargs": kwargs}
        self.rest = _FakeRest()

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        # One page of PRs: #101 closes issues 201 and 202; #102 closes nothing. A null
        # databaseId (a reference to something outside our reach) must be skipped.
        return {
            "repository": {
                "pullRequests": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "databaseId": 101,
                            "closingIssuesReferences": {
                                "nodes": [
                                    {"databaseId": 201},
                                    {"databaseId": 202},
                                    {"databaseId": None},
                                ]
                            },
                        },
                        {
                            "databaseId": 102,
                            "closingIssuesReferences": {"nodes": []},
                        },
                    ],
                }
            }
        }

    def paginate(self, request: Any, **kwargs: Any) -> list[SimpleNamespace]:
        if request is self.rest.pulls.list:
            return [
                ns(id=101, number=1, title="Add feature X", state="open", draft=False,
                   user=ns(login="alice"), html_url="https://x/pull/1",
                   created_at=NOW, updated_at=NOW,
                   # Two requested reviewers plus a ghost/null one that must be dropped.
                   requested_reviewers=[ns(login="frank"), None, ns(login="grace")]),
                ns(id=102, number=2, title="WIP refactor", state="open", draft=True,
                   user=None,  # ghost / deleted author
                   html_url="https://x/pull/2", created_at=NOW, updated_at=NOW,
                   requested_reviewers=[]),
            ]
        if request is self.rest.issues.list_for_repo:
            return [
                ns(id=201, number=3, title="Bug: crash on save", state="open",
                   user=ns(login="carol"), html_url="https://x/issues/3",
                   created_at=NOW, updated_at=NOW, pull_request=None,
                   # Two assignees plus a ghost/null one that must be dropped.
                   assignees=[ns(login="dave"), None, ns(login="erin")]),
                # A pull request returned by the issues endpoint — must be filtered out.
                ns(id=101, number=1, title="Add feature X", state="open",
                   user=ns(login="alice"), html_url="https://x/issues/1",
                   created_at=NOW, updated_at=NOW, pull_request=ns(url="https://x"),
                   assignees=[]),
            ]
        return []


@pytest.fixture
def connector(monkeypatch: pytest.MonkeyPatch) -> GitHubKitConnector:
    monkeypatch.setattr("status_assistant.connectors.github.GitHub", _FakeGitHub)
    return GitHubKitConnector(base_url="https://api.github.com", token="tok", ssl_verify=True)


def test_get_repository_maps_fields(connector: GitHubKitConnector) -> None:
    repo = connector.get_repository("octocat", "hello-world")
    assert repo.id == 1296269
    assert repo.full_name == "octocat/hello-world"
    assert repo.owner == "octocat"
    assert repo.name == "hello-world"
    # The connector stamps which instance the data came from.
    assert repo.github_base_url == "https://api.github.com"


def test_list_pull_requests_maps_fields_and_draft(connector: GitHubKitConnector) -> None:
    prs = [item.pull_request for item in connector.list_pull_requests("octocat", "hello-world")]
    assert [pr.id for pr in prs] == [101, 102]
    assert prs[0].title == "Add feature X"
    assert prs[0].is_draft is False
    assert prs[1].is_draft is True


def test_ghost_author_maps_to_none(connector: GitHubKitConnector) -> None:
    prs = [item.pull_request for item in connector.list_pull_requests("octocat", "hello-world")]
    assert prs[0].author_login == "alice"
    assert prs[1].author_login is None  # user was None


def test_list_pull_requests_maps_requested_reviewers_and_drops_ghosts(
    connector: GitHubKitConnector,
) -> None:
    """Requested reviewers come from ``requested_reviewers``; null/ghost entries are dropped."""
    items = connector.list_pull_requests("octocat", "hello-world")
    assert items[0].requested_reviewer_logins == ["frank", "grace"]
    # A PR with no requested reviewers yields an empty list, not an error.
    assert items[1].requested_reviewer_logins == []


def test_list_issues_excludes_pull_requests(connector: GitHubKitConnector) -> None:
    """The critical GitHub quirk: /issues returns PRs too; we must drop them."""
    issues = connector.list_issues("octocat", "hello-world")
    assert len(issues) == 1
    assert issues[0].issue.id == 201
    assert issues[0].issue.title == "Bug: crash on save"
    # The PR-flavored item (id 101) must not appear as an issue.
    assert 101 not in {iwa.issue.id for iwa in issues}


def test_list_issues_maps_assignees_and_drops_ghosts(connector: GitHubKitConnector) -> None:
    """Assignees come from the plural ``assignees`` array; null/ghost entries are dropped."""
    issues = connector.list_issues("octocat", "hello-world")
    assert issues[0].assignee_logins == ["dave", "erin"]


def test_list_closing_issue_links_maps_pairs_and_drops_null(
    connector: GitHubKitConnector,
) -> None:
    """closingIssuesReferences → (pr_id, issue_id) pairs, skipping null databaseIds."""
    links = connector.list_closing_issue_links("octocat", "hello-world")
    assert links == [(101, 201), (101, 202)]


# --- Activity feed mapping --------------------------------------------------------
#
# A dedicated fake client so the activity fixtures (merge/close timestamps, an out-of-window
# PR, per-PR reviews) don't perturb the open-snapshot tests above. SINCE sits between the
# in-window items and the older one.

SINCE = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
BEFORE = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)  # older than SINCE
AFTER = datetime(2026, 6, 10, 0, 0, tzinfo=UTC)  # newer than SINCE


class _FakeActivityRest:
    class repos:
        @staticmethod
        def get(owner: str, repo: str) -> None: ...

    class pulls:
        @staticmethod
        def list(**kwargs: Any) -> None: ...

        @staticmethod
        def list_reviews(**kwargs: Any) -> None: ...

    class issues:
        @staticmethod
        def list_for_repo(**kwargs: Any) -> None: ...


class _FakeActivityGitHub:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.rest = _FakeActivityRest()

    def paginate(self, request: Any, **kwargs: Any) -> list[SimpleNamespace]:
        if request is self.rest.pulls.list:
            # Sorted by updated_at desc, as the real endpoint is called. #10 merged in-window,
            # #11 closed-unmerged in-window, #12 opened in-window, #13 is older than SINCE (the
            # loop must break before mapping it).
            return [
                ns(id=10, number=10, title="Merged one", state="closed",
                   user=ns(login="alice"), html_url="https://x/pull/10",
                   created_at=BEFORE, updated_at=AFTER, merged_at=AFTER, closed_at=AFTER),
                ns(id=11, number=11, title="Closed one", state="closed",
                   user=ns(login="bob"), html_url="https://x/pull/11",
                   created_at=BEFORE, updated_at=AFTER, merged_at=None, closed_at=AFTER),
                ns(id=12, number=12, title="Opened one", state="open",
                   user=ns(login="carol"), html_url="https://x/pull/12",
                   created_at=AFTER, updated_at=AFTER, merged_at=None, closed_at=None),
                ns(id=13, number=13, title="Ancient", state="closed",
                   user=ns(login="dave"), html_url="https://x/pull/13",
                   created_at=BEFORE, updated_at=BEFORE, merged_at=BEFORE, closed_at=BEFORE),
            ]
        if request is self.rest.pulls.list_reviews:
            # Reviews for the in-window PR #10: one approval in-window, one older (excluded),
            # one PENDING (null submitted_at, excluded).
            if kwargs.get("pull_number") == 10:
                return [
                    ns(id=900, state="APPROVED", submitted_at=AFTER, user=ns(login="erin")),
                    ns(id=901, state="COMMENTED", submitted_at=BEFORE, user=ns(login="frank")),
                    ns(id=902, state="PENDING", submitted_at=None, user=ns(login="grace")),
                ]
            return []
        if request is self.rest.issues.list_for_repo:
            return [
                # Opened in-window.
                ns(id=200, number=20, title="New issue", state="open",
                   user=ns(login="alice"), html_url="https://x/issues/20",
                   created_at=AFTER, updated_at=AFTER, closed_at=None, pull_request=None),
                # Closed in-window (opened before the window).
                ns(id=201, number=21, title="Closed issue", state="closed",
                   user=ns(login="bob"), html_url="https://x/issues/21",
                   created_at=BEFORE, updated_at=AFTER, closed_at=AFTER, pull_request=None),
                # A PR returned by the issues endpoint — must be skipped.
                ns(id=10, number=10, title="Merged one", state="closed",
                   user=ns(login="alice"), html_url="https://x/issues/10",
                   created_at=BEFORE, updated_at=AFTER, closed_at=AFTER,
                   pull_request=ns(url="https://x")),
            ]
        return []


@pytest.fixture
def activity_connector(monkeypatch: pytest.MonkeyPatch) -> GitHubKitConnector:
    monkeypatch.setattr(
        "status_assistant.connectors.github.GitHub", _FakeActivityGitHub
    )
    return GitHubKitConnector(base_url="https://api.github.com", token="tok")


def test_list_activity_since_maps_pr_lifecycle(activity_connector: GitHubKitConnector) -> None:
    records = activity_connector.list_activity_since("o", "n", since=SINCE)
    by_kind = {(r.kind, r.subject_number) for r in records}
    # #10 merged (not also closed), #11 closed-unmerged, #12 opened.
    assert ("pr_merged", 10) in by_kind
    assert ("pr_closed", 10) not in by_kind  # merged takes precedence over closed
    assert ("pr_closed", 11) in by_kind
    assert ("pr_opened", 12) in by_kind
    # #13 is older than SINCE — the desc-sorted loop breaks before it.
    assert all(r.subject_number != 13 for r in records)


def test_list_activity_since_maps_issue_lifecycle(
    activity_connector: GitHubKitConnector,
) -> None:
    records = activity_connector.list_activity_since("o", "n", since=SINCE)
    by_kind = {(r.kind, r.subject_number) for r in records}
    assert ("issue_opened", 20) in by_kind
    assert ("issue_closed", 21) in by_kind
    # The PR returned by the issues endpoint is not stored as an issue event.
    assert ("issue_closed", 10) not in by_kind


def test_list_activity_since_maps_reviews_and_skips_pending_and_old(
    activity_connector: GitHubKitConnector,
) -> None:
    records = activity_connector.list_activity_since("o", "n", since=SINCE)
    reviews = [r for r in records if r.kind == "review_submitted"]
    # Only the in-window APPROVED review on PR #10 survives (old COMMENTED and PENDING dropped).
    assert len(reviews) == 1
    assert reviews[0].subject_number == 10
    assert reviews[0].actor_login == "erin"
    assert reviews[0].detail == "approved"
    assert reviews[0].review_id == 900


def test_enterprise_base_url_is_passed_to_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enterprise support is real logic: the configured base_url must reach the client."""
    monkeypatch.setattr("status_assistant.connectors.github.GitHub", _FakeGitHub)
    GitHubKitConnector(
        base_url="https://ghe.example.com/api/v3", token="tok", ssl_verify=False
    )
    assert _FakeGitHub.last_kwargs["kwargs"]["base_url"] == "https://ghe.example.com/api/v3"
    assert _FakeGitHub.last_kwargs["kwargs"]["ssl_verify"] is False
