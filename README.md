# Engineering Status Assistant

An AI-powered engineering status assistant for tech leads and engineering managers. It
aggregates engineering activity across GitHub repositories (GitHub.com and GitHub
Enterprise Server) into a unified view, so leadership can see what's in progress, what's
blocked, and where to focus — without manually gathering status.

> **Not** a productivity tracker. The goal is engineering visibility and reduced
> coordination overhead.

This is built incrementally in vertical slices. See `AI_Engineering_Status_Assistant_Prompt.md`
for the full product vision.

## Current slice: Engineer View

Everything is still deterministic (no LLM yet). The path, end to end:

1. Configure **N** repositories in `repos.toml`.
2. Sync each repository's **open pull requests** and **open issues** from the GitHub REST
   API (per-repo, or all at once).
3. Persist them to SQLite (an idempotent cache of GitHub state).
4. Serve them via a JSON API.
5. Render a **home dashboard** (every watched repository with its open-PR/issue counts), a
   per-repository **Repository page**, an **Engineers directory**, and a per-engineer
   **Engineer page** showing their open work grouped by repository.

Slice 1 delivered the single-repository Repository View; slice 2 made repositories
first-class and added the dashboard; slice 3 adds the Engineer View. Engineers are a
*derived* axis, computed from what's already cached: a PR belongs to whoever **opened** it
(`author_login`), while an issue belongs to whoever it is **assigned** to (the
`IssueAssignee` table — an issue can have several assignees, so it counts for each). Reviews,
"reviews owed", blockers, and completed work depend on data not yet ingested, and the AI
summary layer (via a LiteLLM proxy) all arrive in later slices.

## Requirements

- Python 3.12+ (developed on 3.14)
- [`uv`](https://docs.astral.sh/uv/)
- A GitHub personal access token with read access to the repo you want to view

## Setup

```bash
uv sync --extra dev
cp .env.example .env
# edit .env: set GITHUB_TOKEN (and GITHUB_BASE_URL for Enterprise)
cp repos.toml.example repos.toml
# edit repos.toml: list the repositories to watch (one [[repos]] table each)
```

**Config split:** secrets and instance connection details live in `.env`; the *set of
repositories to watch* lives in `repos.toml`. Both are git-ignored (they're personal to
your setup) and each ships a committed `*.example` template to copy from. `repos.toml`
holds no secrets — just structural config. All repositories currently use the single
GitHub instance from `.env` — per-repository instance selection is a later, additive
change.

```toml
# repos.toml
[[repos]]
owner = "octocat"
name  = "hello-world"

[[repos]]
owner = "acme"
name  = "api"
```

**Engineer roster (optional):** to limit the Engineers view to specific people, copy
`engineers.toml.example` to `engineers.toml` and list one `[[engineers]]` table per person.
With no `engineers.toml`, the view shows everyone with open work. An engineer is a *person*
identified by their GitHub handle(s); the schema reserves a per-instance handle map so that
when multiple GitHub instances are supported, one person's differing handles can be mapped
without a rewrite.

```toml
# engineers.toml
[[engineers]]
name    = "Octo Cat"    # optional display name
handles = ["octocat"]
```

## Run

```bash
./run.sh            # preflight-checks .env and repos.toml, then starts the dev server
# (equivalently) uv run uvicorn status_assistant.main:app --reload
```

`run.sh` passes any extra arguments through to uvicorn, e.g. `./run.sh --port 9000`.

Then:

```bash
# Sync every repository listed in repos.toml
curl -X POST localhost:8000/api/repositories/sync

# (or) sync a single repository (owner/name in the path)
curl -X POST localhost:8000/api/repositories/<owner>/<name>/sync

# List all synced repositories with their open-work counts (dashboard data)
curl localhost:8000/api/repositories

# Fetch a single repository view as JSON
curl localhost:8000/api/repositories/<owner>/<name>

# List all engineers with open work and their open-PR/issue counts
curl localhost:8000/api/engineers

# Fetch one engineer's open work (grouped by repository) as JSON
curl localhost:8000/api/engineers/<login>
```

Open <http://localhost:8000/> for the home dashboard,
<http://localhost:8000/repositories/<owner>/<name>> for a repository's page,
<http://localhost:8000/engineers> for the engineer directory, and
<http://localhost:8000/engineers/<login>> for an engineer's page.

## Develop

```bash
uv run ruff check .      # lint
uv run mypy src          # type-check
uv run pytest            # tests (no network / no token required)
```

## Architecture

```
repos.toml          # the repositories to watch (structural config, git-ignored; copy from repos.toml.example)
engineers.toml      # optional engineer roster for the Engineers view (git-ignored; copy from engineers.toml.example)
src/status_assistant/
  config.py         # pydantic-settings Settings (env / .env) + load_repos() + load_engineers()
  repos_config.py   # RepoRef + load_repos(): parse/validate repos.toml (stdlib tomllib)
  engineers_config.py # EngineerRef + load_engineers()/allowed_logins(): optional roster filter
  db.py             # SQLite engine + session
  models.py         # SQLModel tables: Repository, PullRequest, Issue, IssueAssignee
  dependencies.py   # get_connector_for(base_url) — connector factory (the instance seam)
  connectors/
    base.py         # GitHubConnector Protocol (the seam)
    github.py       # githubkit-backed implementation (.com + Enterprise)
  ingestion/
    sync.py         # sync_repository() and sync_all(): fetch -> map -> upsert
  queries.py        # repository + engineer read queries — shared by API and web
  api/              # JSON endpoints + response DTOs (repository + engineer routers)
  web/              # Jinja2 dashboard, Repository page, Engineer directory + page
  main.py           # FastAPI app factory
```

The **connector** is abstracted behind a small `Protocol`, and access goes through the
`get_connector_for(base_url)` factory, so additional GitHub instances (and, later, other
sources) can be added without touching the domain or API code. Repositories are first-class
entities throughout — the data model, queries, and API are all keyed by repository, and the
watched set is declared in `repos.toml` rather than hard-coded. A single global GitHub
instance is used today; the `Repository.github_base_url` column and the connector factory
leave multi-instance support as an additive change.

**Engineers** are a *derived* axis, not a stored entity: they're computed from what's already
cached — PRs by their `author_login`, issues by their assignees (`IssueAssignee`) — so the
Engineer View is mostly read-side (a query, a router, and pages). The one stored addition is
the assignee join table, because an issue's assignees are many-valued and can't live on the
issue row. A GitHub login is treated as the identity; a first-class identity model is only
warranted once identities are correlated across sources (Slack/Jira), so it's deferred.
