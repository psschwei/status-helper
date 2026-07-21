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

    def paginate(self, request: Any, **kwargs: Any) -> list[SimpleNamespace]:
        if request is self.rest.pulls.list:
            return [
                ns(id=101, number=1, title="Add feature X", state="open", draft=False,
                   user=ns(login="alice"), html_url="https://x/pull/1",
                   created_at=NOW, updated_at=NOW),
                ns(id=102, number=2, title="WIP refactor", state="open", draft=True,
                   user=None,  # ghost / deleted author
                   html_url="https://x/pull/2", created_at=NOW, updated_at=NOW),
            ]
        if request is self.rest.issues.list_for_repo:
            return [
                ns(id=201, number=3, title="Bug: crash on save", state="open",
                   user=ns(login="carol"), html_url="https://x/issues/3",
                   created_at=NOW, updated_at=NOW, pull_request=None),
                # A pull request returned by the issues endpoint — must be filtered out.
                ns(id=101, number=1, title="Add feature X", state="open",
                   user=ns(login="alice"), html_url="https://x/issues/1",
                   created_at=NOW, updated_at=NOW, pull_request=ns(url="https://x")),
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
    prs = connector.list_pull_requests("octocat", "hello-world")
    assert [pr.id for pr in prs] == [101, 102]
    assert prs[0].title == "Add feature X"
    assert prs[0].is_draft is False
    assert prs[1].is_draft is True


def test_ghost_author_maps_to_none(connector: GitHubKitConnector) -> None:
    prs = connector.list_pull_requests("octocat", "hello-world")
    assert prs[0].author_login == "alice"
    assert prs[1].author_login is None  # user was None


def test_list_issues_excludes_pull_requests(connector: GitHubKitConnector) -> None:
    """The critical GitHub quirk: /issues returns PRs too; we must drop them."""
    issues = connector.list_issues("octocat", "hello-world")
    assert len(issues) == 1
    assert issues[0].id == 201
    assert issues[0].title == "Bug: crash on save"
    # The PR-flavored item (id 101) must not appear as an issue.
    assert 101 not in {issue.id for issue in issues}


def test_enterprise_base_url_is_passed_to_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enterprise support is real logic: the configured base_url must reach the client."""
    monkeypatch.setattr("status_assistant.connectors.github.GitHub", _FakeGitHub)
    GitHubKitConnector(
        base_url="https://ghe.example.com/api/v3", token="tok", ssl_verify=False
    )
    assert _FakeGitHub.last_kwargs["kwargs"]["base_url"] == "https://ghe.example.com/api/v3"
    assert _FakeGitHub.last_kwargs["kwargs"]["ssl_verify"] is False
