# Engineering Status Assistant

An AI-powered engineering status assistant for tech leads and engineering managers. It
aggregates engineering activity across GitHub repositories (GitHub.com and GitHub
Enterprise Server) into a unified view, so leadership can see what's in progress, what's
blocked, and where to focus — without manually gathering status.

> **Not** a productivity tracker. The goal is engineering visibility and reduced
> coordination overhead.

This is built incrementally in vertical slices. See `AI_Engineering_Status_Assistant_Prompt.md`
for the full product vision.

## Current slice: Repository View

Slice 1 is a complete, deterministic (no-LLM) path end to end:

1. Configure **one** repository.
2. Sync its **open pull requests** and **open issues** from the GitHub REST API.
3. Persist them to SQLite (an idempotent cache of GitHub state).
4. Serve them via a JSON API.
5. Render a simple server-side **Repository page**.

The AI summary layer (via a LiteLLM proxy) arrives in a later slice.

## Requirements

- Python 3.12+ (developed on 3.14)
- [`uv`](https://docs.astral.sh/uv/)
- A GitHub personal access token with read access to the repo you want to view

## Setup

```bash
uv sync --extra dev
cp .env.example .env
# edit .env: set GITHUB_TOKEN, REPO_OWNER, REPO_NAME (and GITHUB_BASE_URL for Enterprise)
```

## Run

```bash
uv run uvicorn status_assistant.main:app --reload
```

Then:

```bash
# Trigger a sync for the configured repo (owner/name in the path)
curl -X POST localhost:8000/api/repositories/<owner>/<name>/sync

# Fetch the repository view as JSON
curl localhost:8000/api/repositories/<owner>/<name>
```

Open <http://localhost:8000/repositories/<owner>/<name>> for the HTML view.

## Develop

```bash
uv run ruff check .      # lint
uv run mypy src          # type-check
uv run pytest            # tests (no network / no token required)
```

## Architecture (slice 1)

```
src/status_assistant/
  config.py         # pydantic-settings Settings (env / .env)
  db.py             # SQLite engine + session
  models.py         # SQLModel tables: Repository, PullRequest, Issue
  connectors/
    base.py         # GitHubConnector Protocol (the seam)
    github.py       # githubkit-backed implementation (.com + Enterprise)
  ingestion/
    sync.py         # sync_repository(): fetch -> map -> upsert
  queries.py        # get_repository_view() — shared by API and web
  api/              # JSON endpoints + response DTOs
  web/              # Jinja2 Repository page
  main.py           # FastAPI app factory
```

The **connector** is deliberately abstracted behind a small `Protocol` so additional GitHub
instances (and, later, other sources) can be added without touching the domain or API code.
Repositories are first-class entities in the data model — nothing assumes a single repo.
