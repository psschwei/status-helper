"""Server-rendered pages: the home dashboard and the per-repository page.

Both render from the same read-side queries the JSON API uses. When a repo hasn't been
synced, its page still returns 200 with a friendly "not synced yet" state (an un-synced
repo isn't an error in the browser — it's the expected first-run state).
"""

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from status_assistant.ai.base import AISummarizer
from status_assistant.ai.summarize import generate_engineer_summary
from status_assistant.config import Settings, get_settings
from status_assistant.connectors.base import GitHubConnector
from status_assistant.db import get_session
from status_assistant.dependencies import get_connector, get_optional_summarizer
from status_assistant.engineers_config import allowed_logins
from status_assistant.ingestion.sync import sync_all
from status_assistant.queries import (
    get_engineer_summary,
    get_engineer_view,
    get_repository_view,
    get_whats_happened,
    list_engineers,
    list_repositories,
    list_reviewers,
)
from status_assistant.scrum_config import ScrumSchedule, last_scrum_before

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _format_datetime(value: datetime | None) -> str:
    """Human-readable UTC timestamp for templates."""
    if value is None:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M UTC")


def _format_date(value: datetime | None) -> str:
    """Human-readable UTC date (no time) for templates."""
    if value is None:
        return "—"
    return value.strftime("%Y-%m-%d")


templates.env.filters["datetime"] = _format_datetime
templates.env.filters["date"] = _format_date

router = APIRouter(tags=["web"])

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
ConnectorDep = Annotated[GitHubConnector, Depends(get_connector)]
SummarizerDep = Annotated[AISummarizer | None, Depends(get_optional_summarizer)]


@dataclass(frozen=True)
class DashboardRow:
    """One dashboard row. ``synced`` is False for a configured repo not yet in the cache."""

    owner: str
    name: str
    full_name: str
    html_url: str | None
    last_synced_at: datetime | None
    pull_request_count: int
    issue_count: int
    synced: bool


def _dashboard_rows(session: Session, settings: Settings) -> list[DashboardRow]:
    """Merge configured repos (repos.toml) with synced repos (the cache).

    Every repository in ``repos.toml`` appears — a configured-but-not-yet-synced one shows
    with zero counts and no last-synced time — so the dashboard reflects *intent*, not just
    what has been fetched. Any synced repo no longer in the config is still shown (it has
    cached data worth seeing) rather than silently dropped.
    """
    synced = {item.repository.full_name: item for item in list_repositories(session)}
    configured = {repo.full_name: repo for repo in settings.load_repos()}

    rows: list[DashboardRow] = []
    for full_name in sorted(synced.keys() | configured.keys()):
        item = synced.get(full_name)
        if item is not None:
            repo = item.repository
            rows.append(
                DashboardRow(
                    owner=repo.owner,
                    name=repo.name,
                    full_name=repo.full_name,
                    html_url=repo.html_url,
                    last_synced_at=repo.last_synced_at,
                    pull_request_count=item.pull_request_count,
                    issue_count=item.issue_count,
                    synced=True,
                )
            )
        else:
            ref = configured[full_name]
            rows.append(
                DashboardRow(
                    owner=ref.owner,
                    name=ref.name,
                    full_name=ref.full_name,
                    html_url=None,
                    last_synced_at=None,
                    pull_request_count=0,
                    issue_count=0,
                    synced=False,
                )
            )
    return rows


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: SessionDep, settings: SettingsDep) -> HTMLResponse:
    """Home dashboard: every watched repository with its open-work counts."""
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"rows": _dashboard_rows(session, settings)},
    )


@router.post("/sync")
def sync_all_repositories(
    session: SessionDep, connector: ConnectorDep, settings: SettingsDep
) -> RedirectResponse:
    """Sync every watched repository, then redirect back to the dashboard.

    Backs the dashboard's "Sync all" button. This is a plain form POST that runs the sync
    synchronously (the same ``sync_all`` the JSON API uses) and follows the
    POST-redirect-GET pattern: on success it 303-redirects to ``/`` so a refresh doesn't
    re-submit and the reloaded dashboard shows the fresh counts and last-synced times.
    """
    sync_all(session, connector, settings.load_repos())
    return RedirectResponse(url="/", status_code=303)


@router.get("/repositories/{owner}/{name}", response_class=HTMLResponse)
def repository_page(owner: str, name: str, request: Request, session: SessionDep) -> HTMLResponse:
    view = get_repository_view(session, owner, name)
    return templates.TemplateResponse(
        request,
        "repository.html",
        {"view": view, "owner": owner, "name": name},
    )


@router.get("/engineers", response_class=HTMLResponse)
def engineers_page(request: Request, session: SessionDep, settings: SettingsDep) -> HTMLResponse:
    """Engineer directory: everyone with open work, and their open-work counts.

    Limited to the configured engineer roster when one exists (``engineers.toml``); with no
    roster, shows everyone.
    """
    allowed = allowed_logins(settings.load_engineers())
    return templates.TemplateResponse(
        request,
        "engineers.html",
        {"engineers": list_engineers(session, allowed)},
    )


@router.get("/reviews", response_class=HTMLResponse)
def reviews_page(request: Request, session: SessionDep, settings: SettingsDep) -> HTMLResponse:
    """Reviews directory: everyone with review activity, and their two review counts.

    A landing list over the same per-engineer reviews shown on each engineer's page — each row
    links into that engineer's Reviews section. Limited to the configured engineer roster when
    one exists (``engineers.toml``), the same filter the engineer directory uses.
    """
    allowed = allowed_logins(settings.load_engineers())
    return templates.TemplateResponse(
        request,
        "reviews.html",
        {"reviewers": list_reviewers(session, allowed)},
    )


def _parse_since_web(value: str | None, schedule: ScrumSchedule) -> datetime | None:
    """Parse the ``since`` box's value, or ``None`` to fall back to the computed default.

    The ``<input type="datetime-local">`` submits a naive wall-clock string (``YYYY-MM-DDTHH:MM``)
    the user reads in their scrum timezone, so a naive value is interpreted in that timezone and
    converted to UTC. A fully-qualified value is honored as-is. Anything unparseable returns
    ``None`` so the page degrades gracefully to the default scrum time (the JSON API, by
    contrast, 422s) rather than erroring on a stray keystroke.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=schedule.tzinfo)
    return parsed.astimezone(UTC)


def _since_input_value(since: datetime, schedule: ScrumSchedule) -> str:
    """Format an effective (UTC) ``since`` for the datetime-local box, in the scrum timezone."""
    return since.astimezone(schedule.tzinfo).strftime("%Y-%m-%dT%H:%M")


@router.get("/whats-happened", response_class=HTMLResponse)
def whats_happened_page(
    request: Request, session: SessionDep, settings: SettingsDep, since: str | None = None
) -> HTMLResponse:
    """Activity since the last scrum: what was opened / merged / closed / reviewed.

    The default ``since`` is the most recent scheduled scrum (from ``scrum.toml``, or the
    built-in Mon/Wed/Fri 11:00 ET default). The ``?since=`` box lets the user move the window on
    the fly; an unparseable value falls back to the default. Limited to the configured engineer
    roster when one exists, the same filter the other people-axis views use.
    """
    schedule = settings.load_scrum()
    effective_since = _parse_since_web(since, schedule) or last_scrum_before(
        schedule, datetime.now(UTC)
    )
    allowed = allowed_logins(settings.load_engineers())
    return templates.TemplateResponse(
        request,
        "whats-happened.html",
        {
            "view": get_whats_happened(session, effective_since, allowed),
            "since_input": _since_input_value(effective_since, schedule),
        },
    )


@router.get("/whats-happened/export")
def whats_happened_export(
    session: SessionDep, settings: SettingsDep, since: str | None = None
) -> StreamingResponse:
    """Download the "since last scrum" activity as a CSV file.

    Reuses the same query, ``since`` resolution, and engineer-roster filter as the page above, so
    the export matches exactly what the user sees — one row per deduped :class:`AggregatedActivity`,
    with the engineer, section, date, action, subject, URL, repository, and count. A merged PR's
    nested closed-issues are flattened onto their own ``Issues (linked)`` rows whose ``linked_pr``
    column names the parent PR, so the tree is preserved losslessly. The filename embeds the
    effective ``since`` date so multiple exports don't collide.
    """
    schedule = settings.load_scrum()
    effective_since = _parse_since_web(since, schedule) or last_scrum_before(
        schedule, datetime.now(UTC)
    )
    allowed = allowed_logins(settings.load_engineers())
    view = get_whats_happened(session, effective_since, allowed)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["engineer", "section", "date", "action", "subject", "url", "repository", "count",
         "linked_pr"]
    )
    for eng in view.engineers:
        login = eng.login or "unknown"
        for section, rows in (("PRs", eng.prs), ("Reviews", eng.reviews), ("Issues", eng.issues)):
            for act in rows:
                writer.writerow(
                    [
                        login,
                        section,
                        _format_date(act.latest),
                        act.action_phrase,
                        act.subject_title,
                        act.subject_html_url,
                        act.repository.full_name,
                        act.count,
                        "",  # top-level row: not nested under a PR
                    ]
                )
                # Flatten a merged PR's nested closed-issues onto their own rows, tying each back
                # to the parent via the ``linked_pr`` column so the export stays lossless.
                for child in act.children:
                    writer.writerow(
                        [
                            login,
                            "Issues (linked)",
                            _format_date(child.latest),
                            child.action_phrase,
                            child.subject_title,
                            child.subject_html_url,
                            child.repository.full_name,
                            child.count,
                            act.action_phrase,  # e.g. "merged PR #42"
                        ]
                    )

    buffer.seek(0)
    filename = f"scrum-{effective_since.strftime('%Y-%m-%d')}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/engineers/{login}", response_class=HTMLResponse)
def engineer_page(
    login: str, request: Request, session: SessionDep, settings: SettingsDep
) -> HTMLResponse:
    """Per-engineer page: their open PRs and issues grouped by repository.

    A login with no open work — or one excluded by the engineer roster — returns 200 with a
    friendly empty state (same convention as an un-synced repository page) rather than a 404
    in the browser. Also loads any previously-generated AI summary (see the summary panel)
    without invoking the LLM; generation happens only on the POST below.
    """
    allowed = allowed_logins(settings.load_engineers())
    view = get_engineer_view(session, login, allowed)
    return templates.TemplateResponse(
        request,
        "engineer.html",
        {
            "view": view,
            "login": login,
            "summary": get_engineer_summary(session, login),
            "llm_configured": settings.llm_configured,
        },
    )


@router.post("/engineers/{login}/summary")
def generate_engineer_summary_page(
    login: str, session: SessionDep, settings: SettingsDep, summarizer: SummarizerDep
) -> RedirectResponse:
    """Generate (or regenerate) an engineer's AI summary, then redirect back to their page.

    Backs the "Generate summary" / "Regenerate" button. Follows POST-redirect-GET (like the
    dashboard "Sync all" button): the summary is generated and persisted synchronously, then a
    303 sends the browser back to the engineer page, which renders the fresh summary. When the
    LLM isn't configured, or the login has no work, it simply redirects back — the page shows
    the appropriate state (a "not configured" hint, or the empty state).
    """
    if summarizer is not None:
        allowed = allowed_logins(settings.load_engineers())
        generate_engineer_summary(session, summarizer, settings.llm_model, login, allowed)
    return RedirectResponse(url=f"/engineers/{login}", status_code=303)
