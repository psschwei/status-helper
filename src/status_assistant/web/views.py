"""Server-rendered Repository page.

Renders from the same ``get_repository_view`` query the JSON API uses. When the repo hasn't
been synced, it still returns 200 with a friendly "not synced yet" page (an un-synced repo
isn't an error in the browser — it's the expected first-run state).
"""

from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from status_assistant.db import get_session
from status_assistant.queries import get_repository_view

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


@router.get("/repositories/{owner}/{name}", response_class=HTMLResponse)
def repository_page(owner: str, name: str, request: Request, session: SessionDep) -> HTMLResponse:
    view = get_repository_view(session, owner, name)
    return templates.TemplateResponse(
        request,
        "repository.html",
        {"view": view, "owner": owner, "name": name},
    )
