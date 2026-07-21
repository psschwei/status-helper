"""Tests for the API endpoints and the HTML view.

Drives the app through ``TestClient`` with the connector overridden by a
``FakeGitHubConnector``, so the full request path (sync -> persist -> query -> serialize) is
exercised without touching GitHub.
"""

from collections.abc import Callable

from fastapi.testclient import TestClient

from tests.conftest import (
    FakeGitHubConnector,
    make_issue,
    make_pull_request,
    make_repository,
)

InstallConnector = Callable[[FakeGitHubConnector], None]

REPO_PATH = "/api/repositories/octocat/hello-world"
WEB_PATH = "/repositories/octocat/hello-world"


def _connector() -> FakeGitHubConnector:
    return FakeGitHubConnector(
        repository=make_repository(),
        pull_requests=[
            make_pull_request(101, 1, "Add feature X"),
            make_pull_request(102, 2, "WIP refactor", is_draft=True, author_login=None),
        ],
        issues=[make_issue(201, 3, "Bug: crash on save")],
    )


def test_sync_returns_summary(client: TestClient, use_connector: InstallConnector) -> None:
    use_connector(_connector())

    resp = client.post(f"{REPO_PATH}/sync")

    assert resp.status_code == 200
    body = resp.json()
    assert body["full_name"] == "octocat/hello-world"
    assert body["pull_requests"] == 2
    assert body["issues"] == 1
    assert body["last_synced_at"] is not None


def test_view_returns_active_work_after_sync(
    client: TestClient, use_connector: InstallConnector
) -> None:
    use_connector(_connector())
    client.post(f"{REPO_PATH}/sync")

    resp = client.get(REPO_PATH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["repository"]["full_name"] == "octocat/hello-world"
    assert body["repository"]["last_synced_at"] is not None
    assert len(body["active_pull_requests"]) == 2
    assert len(body["active_issues"]) == 1

    pr_numbers = {pr["number"] for pr in body["active_pull_requests"]}
    assert pr_numbers == {1, 2}
    draft_pr = next(pr for pr in body["active_pull_requests"] if pr["number"] == 2)
    assert draft_pr["is_draft"] is True
    assert draft_pr["author_login"] is None


def test_view_404_before_sync(client: TestClient) -> None:
    resp = client.get(REPO_PATH)
    assert resp.status_code == 404
    assert "not been synced" in resp.json()["detail"]


def test_html_page_renders_after_sync(
    client: TestClient, use_connector: InstallConnector
) -> None:
    use_connector(_connector())
    client.post(f"{REPO_PATH}/sync")

    resp = client.get(WEB_PATH)

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    html = resp.text
    assert "octocat/hello-world" in html
    assert "Add feature X" in html  # a PR title
    assert "Bug: crash on save" in html  # an issue title
    assert "draft" in html  # the draft badge


def test_html_page_renders_before_sync(client: TestClient) -> None:
    """An un-synced repo is a normal first-run state in the browser, not a 404."""
    resp = client.get(WEB_PATH)
    assert resp.status_code == 200
    assert "hasn't been synced yet" in resp.text
