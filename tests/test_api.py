"""Tests for the API endpoints and the HTML view.

Drives the app through ``TestClient`` with the connector overridden by a
``FakeGitHubConnector``, so the full request path (sync -> persist -> query -> serialize) is
exercised without touching GitHub.
"""

from collections.abc import Callable

from fastapi.testclient import TestClient

from tests.conftest import (
    FakeGitHubConnector,
    FakeMultiRepoConnector,
    make_issue,
    make_pull_request,
    make_repository,
)

InstallConnector = Callable[[FakeGitHubConnector | FakeMultiRepoConnector], None]
InstallRepos = Callable[[list[tuple[str, str]]], None]

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


# --- Dashboard: multiple repositories ---------------------------------------------

def _multi_connector() -> FakeMultiRepoConnector:
    return FakeMultiRepoConnector(
        {
            ("octocat", "hello-world"): FakeGitHubConnector(
                repository=make_repository(
                    id=1, owner="octocat", name="hello-world",
                    full_name="octocat/hello-world",
                ),
                pull_requests=[make_pull_request(101, 1, "Add feature X")],
                issues=[make_issue(201, 2, "Bug: crash")],
            ),
            ("acme", "api"): FakeGitHubConnector(
                repository=make_repository(
                    id=2, owner="acme", name="api", full_name="acme/api",
                ),
                pull_requests=[
                    make_pull_request(102, 1, "Refactor"),
                    make_pull_request(103, 2, "Docs"),
                ],
            ),
        }
    )


def test_sync_all_syncs_every_configured_repo(
    client: TestClient, use_connector: InstallConnector, use_repos: InstallRepos
) -> None:
    use_connector(_multi_connector())
    use_repos([("octocat", "hello-world"), ("acme", "api")])

    resp = client.post("/api/repositories/sync")

    assert resp.status_code == 200
    body = resp.json()
    assert {r["full_name"] for r in body} == {"octocat/hello-world", "acme/api"}


def test_list_repositories_returns_counts(
    client: TestClient, use_connector: InstallConnector, use_repos: InstallRepos
) -> None:
    use_connector(_multi_connector())
    use_repos([("octocat", "hello-world"), ("acme", "api")])
    client.post("/api/repositories/sync")

    resp = client.get("/api/repositories")

    assert resp.status_code == 200
    body = resp.json()
    counts = {
        r["repository"]["full_name"]: (r["pull_request_count"], r["issue_count"])
        for r in body
    }
    assert counts == {"octocat/hello-world": (1, 1), "acme/api": (2, 0)}


def test_dashboard_shows_synced_and_unsynced_repos(
    client: TestClient, use_connector: InstallConnector, use_repos: InstallRepos
) -> None:
    # Configure two repos but sync only one; the other is configured-but-unsynced.
    use_connector(
        FakeMultiRepoConnector(
            {
                ("octocat", "hello-world"): FakeGitHubConnector(
                    repository=make_repository(
                        id=1, owner="octocat", name="hello-world",
                        full_name="octocat/hello-world",
                    ),
                    pull_requests=[make_pull_request(101, 1, "Add feature X")],
                ),
            }
        )
    )
    use_repos([("octocat", "hello-world"), ("acme", "api")])
    client.post(f"{REPO_PATH}/sync")

    resp = client.get("/")

    assert resp.status_code == 200
    html = resp.text
    assert "octocat/hello-world" in html  # synced repo
    assert "acme/api" in html  # configured but not yet synced
    assert "not synced" in html  # the badge on the unsynced repo


# --- Engineers -------------------------------------------------------------------

# In _multi_connector, make_pull_request defaults author to "alice" and make_issue to
# "carol": alice authors PRs in both repos, carol authors the one issue.


def test_list_engineers_endpoint(
    client: TestClient, use_connector: InstallConnector, use_repos: InstallRepos
) -> None:
    use_connector(_multi_connector())
    use_repos([("octocat", "hello-world"), ("acme", "api")])
    client.post("/api/repositories/sync")

    resp = client.get("/api/engineers")

    assert resp.status_code == 200
    counts = {
        e["login"]: (e["pull_request_count"], e["issue_count"]) for e in resp.json()
    }
    # alice: 3 PRs (1 + 2) across both repos; carol: 1 issue.
    assert counts == {"alice": (3, 0), "carol": (0, 1)}


def test_engineer_view_endpoint_groups_per_repo(
    client: TestClient, use_connector: InstallConnector, use_repos: InstallRepos
) -> None:
    use_connector(_multi_connector())
    use_repos([("octocat", "hello-world"), ("acme", "api")])
    client.post("/api/repositories/sync")

    resp = client.get("/api/engineers/alice")

    assert resp.status_code == 200
    body = resp.json()
    assert body["login"] == "alice"
    assert body["pull_request_count"] == 3
    per_repo = {r["repository"]["full_name"]: len(r["pull_requests"]) for r in body["repos"]}
    assert per_repo == {"octocat/hello-world": 1, "acme/api": 2}


def test_engineer_view_404_for_unknown_login(
    client: TestClient, use_connector: InstallConnector, use_repos: InstallRepos
) -> None:
    use_connector(_multi_connector())
    use_repos([("octocat", "hello-world"), ("acme", "api")])
    client.post("/api/repositories/sync")

    resp = client.get("/api/engineers/nobody")

    assert resp.status_code == 404
    assert "nobody" in resp.json()["detail"]


def test_engineers_html_pages_render(
    client: TestClient, use_connector: InstallConnector, use_repos: InstallRepos
) -> None:
    use_connector(_multi_connector())
    use_repos([("octocat", "hello-world"), ("acme", "api")])
    client.post("/api/repositories/sync")

    directory = client.get("/engineers")
    assert directory.status_code == 200
    assert "alice" in directory.text

    page = client.get("/engineers/alice")
    assert page.status_code == 200
    assert "octocat/hello-world" in page.text
    assert "acme/api" in page.text


def test_engineer_html_page_empty_state(client: TestClient) -> None:
    """A login with no work is a friendly 200 empty state in the browser, not a 404."""
    resp = client.get("/engineers/nobody")
    assert resp.status_code == 200
    assert "No open work" in resp.text
