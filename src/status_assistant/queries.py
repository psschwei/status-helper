"""Read-side queries against the local cache.

Kept separate from both the API and the web layer so the JSON endpoint and the HTML page
render from the *same* query, never from one calling the other over HTTP. As more views
arrive, this is where their queries live.
"""

from dataclasses import dataclass

from sqlmodel import Session, col, select

from status_assistant.models import Issue, PullRequest, Repository


@dataclass(frozen=True)
class RepositoryView:
    """Everything the Repository page needs: the repo plus its active work."""

    repository: Repository
    active_pull_requests: list[PullRequest]
    active_issues: list[Issue]


def get_repository_view(session: Session, owner: str, name: str) -> RepositoryView | None:
    """Return the Repository view for ``owner/name``, or ``None`` if it hasn't been synced.

    "Active" is simply what's stored: ingestion only persists open PRs and issues (see
    ``ingestion/sync.py``), so no state filtering is needed here yet. Results are ordered
    most-recently-updated first, which is the most useful default for a status view.
    """
    repository = session.exec(
        select(Repository).where(
            col(Repository.owner) == owner, col(Repository.name) == name
        )
    ).first()
    if repository is None:
        return None

    pull_requests = list(
        session.exec(
            select(PullRequest)
            .where(col(PullRequest.repository_id) == repository.id)
            .order_by(col(PullRequest.updated_at).desc())
        ).all()
    )
    issues = list(
        session.exec(
            select(Issue)
            .where(col(Issue.repository_id) == repository.id)
            .order_by(col(Issue.updated_at).desc())
        ).all()
    )

    return RepositoryView(
        repository=repository,
        active_pull_requests=pull_requests,
        active_issues=issues,
    )
