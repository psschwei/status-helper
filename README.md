# Engineering Status Assistant

An AI-powered engineering status assistant for tech leads and engineering managers. It
aggregates engineering activity across GitHub repositories (GitHub.com and GitHub
Enterprise Server) into a unified view, so leadership can see what's in progress, what's
blocked, and where to focus — without manually gathering status.

> **Not** a productivity tracker. The goal is engineering visibility and reduced
> coordination overhead.

This is built incrementally in vertical slices. See `AI_Engineering_Status_Assistant_Prompt.md`
for the full product vision.

## Current slice: Multiple Repositories + Home Dashboard

Everything is still deterministic (no LLM yet). The path, end to end:

1. Configure **N** repositories in `repos.toml`.
2. Sync each repository's **open pull requests** and **open issues** from the GitHub REST
   API (per-repo, or all at once).
3. Persist them to SQLite (an idempotent cache of GitHub state).
4. Serve them via a JSON API.
5. Render a **home dashboard** listing every watched repository with its open-PR/issue
   counts, and a per-repository **Repository page**.

Slice 1 delivered the single-repository Repository View; slice 2 makes repositories
first-class and adds the dashboard. The AI summary layer (via a LiteLLM proxy) arrives in a
later slice.

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

## Run

```bash
uv run uvicorn status_assistant.main:app --reload
```

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
```

Open <http://localhost:8000/> for the home dashboard, and
<http://localhost:8000/repositories/<owner>/<name>> for a repository's page.

## Develop

```bash
uv run ruff check .      # lint
uv run mypy src          # type-check
uv run pytest            # tests (no network / no token required)
```

## Architecture

```
repos.toml          # the repositories to watch (structural config, git-ignored; copy from repos.toml.example)
src/status_assistant/
  config.py         # pydantic-settings Settings (env / .env) + load_repos()
  repos_config.py   # RepoRef + load_repos(): parse/validate repos.toml (stdlib tomllib)
  db.py             # SQLite engine + session
  models.py         # SQLModel tables: Repository, PullRequest, Issue
  dependencies.py   # get_connector_for(base_url) — connector factory (the instance seam)
  connectors/
    base.py         # GitHubConnector Protocol (the seam)
    github.py       # githubkit-backed implementation (.com + Enterprise)
  ingestion/
    sync.py         # sync_repository() and sync_all(): fetch -> map -> upsert
  queries.py        # list_repositories() + get_repository_view() — shared by API and web
  api/              # JSON endpoints + response DTOs
  web/              # Jinja2 dashboard + Repository page
  main.py           # FastAPI app factory
```

The **connector** is abstracted behind a small `Protocol`, and access goes through the
`get_connector_for(base_url)` factory, so additional GitHub instances (and, later, other
sources) can be added without touching the domain or API code. Repositories are first-class
entities throughout — the data model, queries, and API are all keyed by repository, and the
watched set is declared in `repos.toml` rather than hard-coded. A single global GitHub
instance is used today; the `Repository.github_base_url` column and the connector factory
leave multi-instance support as an additive change.
