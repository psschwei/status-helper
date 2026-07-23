"""JSON API endpoints for the Repository view.

Two capabilities: trigger a sync, and read the view. Paths are repo-scoped
(``/api/repositories/{owner}/{name}``) so additional repositories slot in later without a
redesign, even though slice 1 only wires the configured one.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from status_assistant.ai.base import AISummarizer
from status_assistant.ai.summarize import generate_engineer_summary
from status_assistant.api.schemas import (
    EngineerListItemOut,
    EngineerSummaryOut,
    EngineerViewOut,
    RepositoryListItemOut,
    RepositoryViewOut,
    ReviewerListItemOut,
    SyncResultOut,
    WhatsHappenedOut,
)
from status_assistant.config import Settings, get_settings
from status_assistant.connectors.base import GitHubConnector
from status_assistant.db import get_session
from status_assistant.dependencies import get_connector, get_optional_summarizer
from status_assistant.engineers_config import allowed_logins
from status_assistant.ingestion.sync import (
    prune_activity_events,
    sync_all,
    sync_repository,
)
from status_assistant.queries import (
    get_engineer_summary,
    get_engineer_view,
    get_repository_view,
    get_whats_happened,
    list_engineers,
    list_repositories,
    list_reviewers,
)
from status_assistant.scrum_config import last_scrum_before

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
    # Trim old activity history on this single-repo sync too, so the append-only table stays
    # bounded regardless of which sync entry point is used (sync_all prunes on its own).
    prune_activity_events(session)
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


# Reviews are another *derived* axis over the cached PRs and review requests (keyed by reviewer
# login), so like engineers they need no ingestion of their own — a separate router again.
reviews_router = APIRouter(prefix="/api/reviews", tags=["reviews"])


@reviews_router.get("", response_model=list[ReviewerListItemOut])
def list_reviewers_view(session: SessionDep, settings: SettingsDep) -> list[ReviewerListItemOut]:
    """Return every engineer with review activity, with their two review counts.

    Limited to the configured engineer roster (``engineers.toml``) when one exists, the same
    filter the engineer directory uses.
    """
    allowed = allowed_logins(settings.load_engineers())
    return [ReviewerListItemOut.from_item(item) for item in list_reviewers(session, allowed)]


# Activity ("what's happened since last scrum?") is a *derived* axis over the append-only
# ActivityEvent log — no ingestion of its own, so a separate router again, like engineers/reviews.
activity_router = APIRouter(prefix="/api/whats-happened", tags=["activity"])


@activity_router.get("", response_model=WhatsHappenedOut)
def whats_happened_view(
    session: SessionDep, settings: SettingsDep, since: str | None = None
) -> WhatsHappenedOut:
    """Return activity events since ``since`` (default: the most recent scheduled scrum).

    ``since`` accepts an ISO-8601 datetime; a naive value is treated as UTC. An unparseable
    value is a 422 — a clean contract for API callers (the HTML page, by contrast, falls back
    to the default). Limited to the configured engineer roster when one exists.
    """
    if since is None:
        effective_since = last_scrum_before(settings.load_scrum(), datetime.now(UTC))
    else:
        try:
            parsed = datetime.fromisoformat(since)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"Invalid 'since' datetime: {since!r}."
            ) from exc
        effective_since = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    allowed = allowed_logins(settings.load_engineers())
    return WhatsHappenedOut.from_view(
        get_whats_happened(session, effective_since, allowed)
    )


SummarizerDep = Annotated[AISummarizer | None, Depends(get_optional_summarizer)]


@engineers_router.get("/{login}/summary", response_model=EngineerSummaryOut)
def engineer_summary(login: str, session: SessionDep) -> EngineerSummaryOut:
    """Return the stored AI summary for an engineer.

    404 if none has been generated yet — POST to this same path to create one.
    """
    summary = get_engineer_summary(session, login)
    if summary is None:
        raise HTTPException(
            status_code=404,
            detail=f"No summary generated yet for engineer '{login}'.",
        )
    return EngineerSummaryOut.model_validate(summary)


@engineers_router.post("/{login}/summary", response_model=EngineerSummaryOut)
def generate_summary(
    login: str, session: SessionDep, settings: SettingsDep, summarizer: SummarizerDep
) -> EngineerSummaryOut:
    """Generate (or regenerate) and persist an engineer's AI status summary.

    503 if the LLM isn't configured; 404 if the login has no open work or is excluded by the
    roster (same condition as the engineer view).
    """
    if summarizer is None:
        raise HTTPException(
            status_code=503,
            detail="AI summaries are not configured (set LLM_API_KEY).",
        )
    allowed = allowed_logins(settings.load_engineers())
    summary = generate_engineer_summary(
        session, summarizer, settings.llm_model, login, allowed
    )
    if summary is None:
        raise HTTPException(
            status_code=404,
            detail=f"No open work found for engineer '{login}'.",
        )
    return EngineerSummaryOut.model_validate(summary)
