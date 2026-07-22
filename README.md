# Engineering Status Assistant

An AI-powered engineering status assistant for tech leads and engineering managers. It
aggregates engineering activity across GitHub repositories (GitHub.com and GitHub
Enterprise Server) into a unified view, so leadership can see what's in progress, what's
blocked, and where to focus — without manually gathering status.

> **Not** a productivity tracker. The goal is engineering visibility and reduced
> coordination overhead.

This is built incrementally in vertical slices. See `AI_Engineering_Status_Assistant_Prompt.md`
for the full product vision.

## Current slice: Reviews as a top-level page

This slice ingests each open PR's **currently-requested reviewers** and surfaces two
review-related bullets from the Engineer View spec — deterministically, no LLM. **Reviews**
is now its own top-level page (alongside Repositories and Engineers): a directory of everyone
with outstanding review activity and their two counts, each linking into that engineer's
Reviews section. On an engineer's page, the **Reviews** panel shows:

- **Reviews you owe** — open PRs where the engineer is still a requested reviewer.
- **Your PRs awaiting review** — the engineer's own open PRs that still have requested
  reviewers (i.e. waiting on someone else).

GitHub removes a reviewer from a PR's requested list the moment they submit a review, so
"still requested" is a naturally-accurate proxy for "review outstanding" — which is why this
slice can ship *requested reviewers only*, without ingesting submitted-review verdicts. Only
*user* reviewers are stored; team/org review requests are out of scope. Requested reviewers
come free in the REST `pulls.list` payload, so no extra API call is needed.

The full path, end to end:

1. Configure **N** repositories in `repos.toml`.
2. Sync each repository's **open pull requests** (with their requested reviewers) and **open
   issues** from the GitHub REST API (per-repo, or all at once).
3. Persist them to SQLite (an idempotent cache of GitHub state).
4. Serve them via a JSON API.
5. Render a **home dashboard** (every watched repository with its open-PR/issue counts), a
   per-repository **Repository page**, an **Engineers directory**, a per-engineer
   **Engineer page** showing their open work grouped by repository (with a **Reviews** panel),
   and a top-level **Reviews directory** of everyone with outstanding review activity.
6. On the Engineer page, a **Generate summary** button asks an LLM to summarize that
   engineer's open work into a short status update, stored and shown on reload (with a
   **Regenerate** button).

Earlier slices: slice 1 delivered the single-repository Repository View; slice 2 made
repositories first-class and added the dashboard; slice 3 added the Engineer View; slice 4
added the first LLM integration (the AI status summary). Engineers are a *derived* axis,
computed from what's already cached: a PR belongs to whoever **opened** it (`author_login`), an
issue belongs to whoever it is **assigned** to (the `IssueAssignee` table), and a review is
owed by whoever is **requested** on a PR (the `PRReviewRequest` table — a PR can request
several reviewers, so it counts for each).

**Division of labor for the AI summary:** deterministic code gathers and shapes the facts (the
engineer's PRs/issues, counts, draft flags, dates); the LLM only *summarizes* them into prose —
it never fetches or computes. The client is accessed through an OpenAI-compatible endpoint (a
LiteLLM proxy, or a provider API), behind an `AISummarizer` protocol that mirrors the
`GitHubConnector` seam. Summaries are **derived output**, not cached GitHub state, so a
repository re-sync does not clear them (a `generated_at` timestamp makes staleness visible).
(The summary prompt does not yet include review load — a natural next increment now that the
data exists.)

Blockers, completed work, and Repository/Team summaries depend on data not yet ingested and
arrive in later slices.

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

**AI summaries (optional):** set `LLM_BASE_URL`, `LLM_MODEL`, and `LLM_API_KEY` in `.env`
to enable the Engineer-page AI summary. Point `LLM_BASE_URL` at any OpenAI-compatible
endpoint — a [LiteLLM proxy](https://docs.litellm.ai/) (recommended; provider-agnostic) or
a provider's own API. Without `LLM_API_KEY` the feature stays disabled: the page shows a
"not configured" hint instead of the generate button, and the API returns `503`.

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

# List everyone with outstanding review activity and their reviews-owed/awaiting counts
curl localhost:8000/api/reviews

# Generate (or regenerate) an engineer's AI status summary (needs LLM_API_KEY; 503 if not)
curl -X POST localhost:8000/api/engineers/<login>/summary

# Fetch a previously-generated summary (404 if none yet)
curl localhost:8000/api/engineers/<login>/summary
```

Open <http://localhost:8000/> for the home dashboard,
<http://localhost:8000/repositories/<owner>/<name>> for a repository's page,
<http://localhost:8000/engineers> for the engineer directory,
<http://localhost:8000/engineers/<login>> for an engineer's page, and
<http://localhost:8000/reviews> for the reviews directory.

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
  models.py         # SQLModel tables: Repository, PullRequest, Issue, IssueAssignee, PRReviewRequest, PullRequestIssueLink, EngineerSummary
  dependencies.py   # get_connector_for(base_url) + get_summarizer/get_optional_summarizer (the seams)
  connectors/
    base.py         # GitHubConnector Protocol (the data-source seam)
    github.py       # githubkit-backed implementation (.com + Enterprise)
  ai/
    base.py         # AISummarizer Protocol + SummaryPrompt (the AI seam)
    openai_client.py# OpenAI-SDK-backed implementation (LiteLLM proxy / any OpenAI-compatible API)
    summarize.py    # build_engineer_summary_prompt() (deterministic) + generate_engineer_summary()
  ingestion/
    sync.py         # sync_repository() and sync_all(): fetch -> map -> upsert
  queries.py        # repository + engineer read queries (incl. get_engineer_summary) — shared by API and web
  api/              # JSON endpoints + response DTOs (repository + engineer routers, incl. summary)
  web/              # Jinja2 dashboard, Repository page, Engineer directory + page (with AI summary panel), Reviews directory
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
cached — PRs by their `author_login`, issues by their assignees (`IssueAssignee`), and reviews
owed by their requested-reviewer rows (`PRReviewRequest`) — so the Engineer View is mostly
read-side (a query, a router, and pages). The stored additions are the join tables
(`IssueAssignee`, `PRReviewRequest`), because assignees and requested reviewers are both
many-valued and can't live on the issue/PR row. `PRReviewRequest` mirrors `IssueAssignee` end
to end: its own table, replaced wholesale on each sync, keyed off ids known at ingest time. A
GitHub login is treated as the identity; a first-class identity model is only warranted once
identities are correlated across sources (Slack/Jira), so it's deferred.

The **AI summarizer** follows the same seam pattern as the connector: `AISummarizer` is a
narrow `Protocol` (prose-in via `SummaryPrompt`, prose-out), and `OpenAISummarizer` is the
only place that knows about the LLM SDK — swap providers by pointing `LLM_BASE_URL` at a
different endpoint, not by touching call sites. The prompt is built by a **pure, deterministic**
function (`build_engineer_summary_prompt`) that's unit-tested with no network, so the "what we
tell the model" logic is verified in isolation and reused when Repository/Team summaries arrive.
The one stored addition is `EngineerSummary` (keyed by `login`; regenerate is an upsert), which
is generated output rather than cached GitHub state — so it survives a re-sync.
