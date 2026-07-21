"""JSON API endpoints for the Repository view.

Two capabilities: trigger a sync, and read the view. Paths are repo-scoped
(``/api/repositories/{owner}/{name}``) so additional repositories slot in later without a
redesign, even though slice 1 only wires the configured one.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from status_assistant.api.schemas import RepositoryViewOut, SyncResultOut
from status_assistant.connectors.base import GitHubConnector
from status_assistant.db import get_session
from status_assistant.dependencies import get_connector
from status_assistant.ingestion.sync import sync_repository
from status_assistant.queries import get_repository_view

router = APIRouter(prefix="/api/repositories", tags=["repositories"])

SessionDep = Annotated[Session, Depends(get_session)]
ConnectorDep = Annotated[GitHubConnector, Depends(get_connector)]


@router.post("/{owner}/{name}/sync", response_model=SyncResultOut)
def sync(owner: str, name: str, session: SessionDep, connector: ConnectorDep) -> SyncResultOut:
    """Fetch the repository's open PRs and issues from GitHub and persist them."""
    result = sync_repository(session, connector, owner, name)
    return SyncResultOut.model_validate(result)


@router.get("/{owner}/{name}", response_model=RepositoryViewOut)
def repository_view(owner: str, name: str, session: SessionDep) -> RepositoryViewOut:
    """Return the stored active PRs and issues for a repository.

    404 if it hasn't been synced yet — prompting the caller to POST ``/sync`` first.
    """
    view = get_repository_view(session, owner, name)
    if view is None:
        raise HTTPException(
            status_code=404,
            detail=f"Repository '{owner}/{name}' has not been synced yet.",
        )
    return RepositoryViewOut.from_view(view)
