"""Server-rendered pages: the home dashboard and the per-repository page.

Both render from the same read-side queries the JSON API uses. When a repo hasn't been
synced, its page still returns 200 with a friendly "not synced yet" state (an un-synced
repo isn't an error in the browser — it's the expected first-run state).
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from status_assistant.config import Settings, get_settings
from status_assistant.connectors.base import GitHubConnector
from status_assistant.db import get_session
from status_assistant.dependencies import get_connector
from status_assistant.engineers_config import allowed_logins
from status_assistant.ingestion.sync import sync_all
from status_assistant.queries import (
    get_engineer_view,
    get_repository_view,
    list_engineers,
    list_repositories,
)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _format_datetime(value: datetime | None) -> str:
    """Human-readable UTC timestamp for templates."""
    if value is None:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M UTC")


templates.env.filters["datetime"] = _format_datetime

router = APIRouter(tags=["web"])

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
ConnectorDep = Annotated[GitHubConnector, Depends(get_connector)]


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


@router.get("/engineers/{login}", response_class=HTMLResponse)
def engineer_page(
    login: str, request: Request, session: SessionDep, settings: SettingsDep
) -> HTMLResponse:
    """Per-engineer page: their open PRs and issues grouped by repository.

    A login with no open work — or one excluded by the engineer roster — returns 200 with a
    friendly empty state (same convention as an un-synced repository page) rather than a 404
    in the browser.
    """
    allowed = allowed_logins(settings.load_engineers())
    view = get_engineer_view(session, login, allowed)
    return templates.TemplateResponse(
        request,
        "engineer.html",
        {"view": view, "login": login},
    )
