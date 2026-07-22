"""Tests for the API endpoints and the HTML view.

Drives the app through ``TestClient`` with the connector overridden by a
``FakeGitHubConnector``, so the full request path (sync -> persist -> query -> serialize) is
exercised without touching GitHub.
"""

from collections.abc import Callable

from fastapi.testclient import TestClient

from status_assistant.engineers_config import EngineerRef
from tests.conftest import (
    FakeGitHubConnector,
    FakeMultiRepoConnector,
    FakeSummarizer,
    make_issue,
    make_pull_request,
    make_repository,
)

InstallConnector = Callable[[FakeGitHubConnector | FakeMultiRepoConnector], None]
InstallRepos = Callable[[list[tuple[str, str]]], None]
InstallEngineers = Callable[[list[EngineerRef]], None]
InstallSummarizer = Callable[[FakeSummarizer], None]

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
                # The issue is *assigned* to carol (that's what the engineer view counts).
                assignees={201: ["carol"]},
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


def test_dashboard_has_sync_button(
    client: TestClient, use_connector: InstallConnector, use_repos: InstallRepos
) -> None:
    use_connector(_multi_connector())
    use_repos([("octocat", "hello-world")])

    html = client.get("/").text

    # The "Sync all" button posts to the web sync route.
    assert 'action="/sync"' in html
    assert "Sync all" in html


def test_web_sync_button_syncs_and_redirects(
    client: TestClient, use_connector: InstallConnector, use_repos: InstallRepos
) -> None:
    use_connector(_multi_connector())
    use_repos([("octocat", "hello-world"), ("acme", "api")])

    # POST-redirect-GET: the button posts to /sync, which 303s back to the dashboard.
    resp = client.post("/sync", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"

    # The sync actually persisted data: the dashboard now shows both repos' open work.
    dashboard = client.get("/")
    assert "octocat/hello-world" in dashboard.text
    assert "acme/api" in dashboard.text
    # And it's queryable via the read side.
    counts = {
        r["repository"]["full_name"]: (r["pull_request_count"], r["issue_count"])
        for r in client.get("/api/repositories").json()
    }
    assert counts == {"octocat/hello-world": (1, 1), "acme/api": (2, 0)}


# --- Engineers -------------------------------------------------------------------

# In _multi_connector, make_pull_request defaults author to "alice": alice authors PRs in
# both repos. The one issue (id 201) is *assigned* to carol — the engineer view counts issues
# by assignee, so carol gets the issue even though make_issue's default author is also "carol".


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
    # No links in this fixture, so every PR is in the "PRs without an issue" section.
    per_repo = {
        r["repository"]["full_name"]: len(r["prs_without_issue"]) for r in body["repos"]
    }
    assert per_repo == {"octocat/hello-world": 1, "acme/api": 2}


def test_engineer_view_endpoint_pairs_linked_issue_and_pr(
    client: TestClient, use_connector: InstallConnector, use_repos: InstallRepos
) -> None:
    """End-to-end: a PR that closes an assigned issue surfaces as a pair in the JSON."""
    use_connector(
        FakeGitHubConnector(
            repository=make_repository(id=1, owner="octocat", name="hello-world",
                                       full_name="octocat/hello-world"),
            pull_requests=[make_pull_request(101, 1, "Fix bug", author_login="alice")],
            issues=[make_issue(201, 2, "The bug")],
            assignees={201: ["alice"]},
            links=[(101, 201)],
        )
    )
    use_repos([("octocat", "hello-world")])
    client.post("/api/repositories/sync")

    resp = client.get("/api/engineers/alice")

    assert resp.status_code == 200
    work = resp.json()["repos"][0]
    assert len(work["paired"]) == 1
    assert work["paired"][0]["issue"]["number"] == 2
    assert work["paired"][0]["pull_request"]["number"] == 1
    assert work["issues_without_pr"] == []
    assert work["prs_without_issue"] == []


def test_engineer_view_404_for_unknown_login(
    client: TestClient, use_connector: InstallConnector, use_repos: InstallRepos
) -> None:
    use_connector(_multi_connector())
    use_repos([("octocat", "hello-world"), ("acme", "api")])
    client.post("/api/repositories/sync")

    resp = client.get("/api/engineers/nobody")

    assert resp.status_code == 404
    assert "nobody" in resp.json()["detail"]


def test_engineer_issues_are_by_assignee_not_author(
    client: TestClient, use_connector: InstallConnector, use_repos: InstallRepos
) -> None:
    """An issue authored by one person but assigned to another belongs to the assignee."""
    use_connector(
        FakeGitHubConnector(
            repository=make_repository(id=1, owner="octocat", name="hello-world",
                                       full_name="octocat/hello-world"),
            # Authored by alice (make_issue default is "carol", override it), assigned to bob.
            issues=[make_issue(201, 1, "Bug", author_login="alice")],
            assignees={201: ["bob"]},
        )
    )
    use_repos([("octocat", "hello-world")])
    client.post("/api/repositories/sync")

    # bob (assignee) has the issue; alice (author) does not.
    bob = client.get("/api/engineers/bob")
    assert bob.status_code == 200
    assert bob.json()["issue_count"] == 1
    assert client.get("/api/engineers/alice").status_code == 404


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


def test_engineers_endpoint_filtered_by_roster(
    client: TestClient,
    use_connector: InstallConnector,
    use_repos: InstallRepos,
    use_engineers: InstallEngineers,
) -> None:
    use_connector(_multi_connector())
    use_repos([("octocat", "hello-world"), ("acme", "api")])
    # Roster includes only alice; carol has work but is excluded.
    use_engineers([EngineerRef(name="Alice", handles=["alice"])])
    client.post("/api/repositories/sync")

    listed = client.get("/api/engineers")
    assert [e["login"] for e in listed.json()] == ["alice"]

    # alice (in roster) resolves; carol (excluded) 404s even though she has open work.
    assert client.get("/api/engineers/alice").status_code == 200
    assert client.get("/api/engineers/carol").status_code == 404


def test_engineers_page_filtered_by_roster(
    client: TestClient,
    use_connector: InstallConnector,
    use_repos: InstallRepos,
    use_engineers: InstallEngineers,
) -> None:
    use_connector(_multi_connector())
    use_repos([("octocat", "hello-world"), ("acme", "api")])
    use_engineers([EngineerRef(handles=["alice"])])
    client.post("/api/repositories/sync")

    directory = client.get("/engineers")
    assert "alice" in directory.text
    assert "carol" not in directory.text

    # An excluded engineer's page shows the friendly empty state, not their work.
    page = client.get("/engineers/carol")
    assert page.status_code == 200
    assert "No open work found" in page.text


# --- AI summaries ----------------------------------------------------------------


def test_generate_summary_endpoint_persists_and_reads_back(
    client: TestClient,
    use_connector: InstallConnector,
    use_repos: InstallRepos,
    use_summarizer: InstallSummarizer,
) -> None:
    use_connector(_multi_connector())
    use_repos([("octocat", "hello-world"), ("acme", "api")])
    use_summarizer(FakeSummarizer())
    client.post("/api/repositories/sync")

    # Generate: returns the canned summary and records provenance.
    resp = client.post("/api/engineers/alice/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["login"] == "alice"
    assert body["summary_text"].startswith(FakeSummarizer.MARKER)
    assert "octocat/hello-world" in body["summary_text"]  # facts flowed into the prompt
    assert body["generated_at"] is not None

    # And it's readable back via GET without regenerating.
    got = client.get("/api/engineers/alice/summary")
    assert got.status_code == 200
    assert got.json()["summary_text"] == body["summary_text"]


def test_get_summary_404_before_generation(
    client: TestClient, use_connector: InstallConnector, use_repos: InstallRepos
) -> None:
    use_connector(_multi_connector())
    use_repos([("octocat", "hello-world")])
    client.post("/api/repositories/sync")

    resp = client.get("/api/engineers/alice/summary")
    assert resp.status_code == 404
    assert "No summary" in resp.json()["detail"]


def test_generate_summary_regenerates_overwriting(
    client: TestClient,
    use_connector: InstallConnector,
    use_repos: InstallRepos,
    use_summarizer: InstallSummarizer,
) -> None:
    use_connector(_multi_connector())
    use_repos([("octocat", "hello-world"), ("acme", "api")])
    use_summarizer(FakeSummarizer())
    client.post("/api/repositories/sync")

    first = client.post("/api/engineers/alice/summary").json()
    second = client.post("/api/engineers/alice/summary").json()

    # Still exactly one summary for alice (upsert-by-login), and GET returns the latest.
    assert first["login"] == second["login"] == "alice"
    got = client.get("/api/engineers/alice/summary").json()
    assert got["summary_text"] == second["summary_text"]


def test_generate_summary_404_for_unknown_login(
    client: TestClient,
    use_connector: InstallConnector,
    use_repos: InstallRepos,
    use_summarizer: InstallSummarizer,
) -> None:
    use_connector(_multi_connector())
    use_repos([("octocat", "hello-world")])
    use_summarizer(FakeSummarizer())
    client.post("/api/repositories/sync")

    resp = client.post("/api/engineers/nobody/summary")
    assert resp.status_code == 404


def test_generate_summary_503_when_llm_not_configured(
    client: TestClient, use_connector: InstallConnector, use_repos: InstallRepos
) -> None:
    """With no summarizer override installed and no LLM_API_KEY, generation is unavailable."""
    use_connector(_multi_connector())
    use_repos([("octocat", "hello-world")])
    client.post("/api/repositories/sync")

    resp = client.post("/api/engineers/alice/summary")
    assert resp.status_code == 503
    assert "not configured" in resp.json()["detail"]


def test_generate_summary_respects_roster(
    client: TestClient,
    use_connector: InstallConnector,
    use_repos: InstallRepos,
    use_engineers: InstallEngineers,
    use_summarizer: InstallSummarizer,
) -> None:
    use_connector(_multi_connector())
    use_repos([("octocat", "hello-world"), ("acme", "api")])
    use_engineers([EngineerRef(handles=["alice"])])
    use_summarizer(FakeSummarizer())
    client.post("/api/repositories/sync")

    # carol has work but is excluded by the roster -> no summary (404), same as the view.
    assert client.post("/api/engineers/alice/summary").status_code == 200
    assert client.post("/api/engineers/carol/summary").status_code == 404


def test_engineer_page_summary_panel(
    client: TestClient,
    use_connector: InstallConnector,
    use_repos: InstallRepos,
    use_summarizer: InstallSummarizer,
) -> None:
    use_connector(_multi_connector())
    use_repos([("octocat", "hello-world"), ("acme", "api")])
    use_summarizer(FakeSummarizer())
    client.post("/api/repositories/sync")

    # Before generation (LLM configured via the fake override): a Generate button, no text yet.
    page = client.get("/engineers/alice")
    assert "AI Status Summary" in page.text
    assert "Generate summary" in page.text

    # Generate via the web button (POST-redirect-GET), then the page shows the summary + Regenerate.
    resp = client.post("/engineers/alice/summary", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/engineers/alice"

    page = client.get("/engineers/alice")
    assert FakeSummarizer.MARKER in page.text
    assert "Regenerate" in page.text


def test_engineer_page_summary_not_configured(
    client: TestClient, use_connector: InstallConnector, use_repos: InstallRepos
) -> None:
    """No summarizer override + no LLM_API_KEY -> the page shows a hint, not the button."""
    use_connector(_multi_connector())
    use_repos([("octocat", "hello-world")])
    client.post("/api/repositories/sync")

    page = client.get("/engineers/alice")
    assert "not configured" in page.text
    assert "Generate summary" not in page.text
