"""JSON API endpoints for the Repository view.

Two capabilities: trigger a sync, and read the view. Paths are repo-scoped
(``/api/repositories/{owner}/{name}``) so additional repositories slot in later without a
redesign, even though slice 1 only wires the configured one.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from status_assistant.api.schemas import (
    EngineerListItemOut,
    EngineerViewOut,
    RepositoryListItemOut,
    RepositoryViewOut,
    SyncResultOut,
)
from status_assistant.config import Settings, get_settings
from status_assistant.connectors.base import GitHubConnector
from status_assistant.db import get_session
from status_assistant.dependencies import get_connector
from status_assistant.engineers_config import allowed_logins
from status_assistant.ingestion.sync import sync_all, sync_repository
from status_assistant.queries import (
    get_engineer_view,
    get_repository_view,
    list_engineers,
    list_repositories,
)

router = APIRouter(prefix="/api/repositories", tags=["repositories"])

SessionDep = Annotated[Session, Depends(get_session)]
ConnectorDep = Annotated[GitHubConnector, Depends(get_connector)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("", response_model=list[RepositoryListItemOut])
def list_repositories_view(session: SessionDep) -> list[RepositoryListItemOut]:
    """Return every synced repository with its open PR and issue counts (dashboard data)."""
    return [RepositoryListItemOut.from_item(item) for item in list_repositories(session)]


@router.post("/sync", response_model=list[SyncResultOut])
def sync_all_repositories(
    session: SessionDep, connector: ConnectorDep, settings: SettingsDep
) -> list[SyncResultOut]:
    """Fetch and persist open PRs and issues for every repository in ``repos.toml``."""
    results = sync_all(session, connector, settings.load_repos())
    return [SyncResultOut.model_validate(r) for r in results]


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


# Engineers are a *derived* axis over the same cached PRs/issues (keyed by author login), so
# these routes need no ingestion of their own — hence a separate router under a different
# prefix rather than a nested repository path.
engineers_router = APIRouter(prefix="/api/engineers", tags=["engineers"])


@engineers_router.get("", response_model=list[EngineerListItemOut])
def list_engineers_view(session: SessionDep, settings: SettingsDep) -> list[EngineerListItemOut]:
    """Return every engineer with open work, with their open PR and issue counts.

    Limited to the configured engineer roster (``engineers.toml``) when one exists.
    """
    allowed = allowed_logins(settings.load_engineers())
    return [EngineerListItemOut.from_item(item) for item in list_engineers(session, allowed)]


@engineers_router.get("/{login}", response_model=EngineerViewOut)
def engineer_view(login: str, session: SessionDep, settings: SettingsDep) -> EngineerViewOut:
    """Return an engineer's open PRs and issues, grouped by repository.

    404 if the login has no open work in the cache — either they have none, the relevant
    repositories haven't been synced yet, or they are excluded by the engineer roster.
    """
    allowed = allowed_logins(settings.load_engineers())
    view = get_engineer_view(session, login, allowed)
    if view is None:
        raise HTTPException(
            status_code=404,
            detail=f"No open work found for engineer '{login}'.",
        )
    return EngineerViewOut.from_view(view)
