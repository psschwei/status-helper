"""Read-side queries against the local cache.

Kept separate from both the API and the web layer so the JSON endpoint and the HTML page
render from the *same* query, never from one calling the other over HTTP. As more views
arrive, this is where their queries live.
"""

from dataclasses import dataclass

from sqlmodel import Session, col, func, select

from status_assistant.models import Issue, PullRequest, Repository


@dataclass(frozen=True)
class RepositoryListItem:
    """A repository plus its open-work counts, for the home dashboard list."""

    repository: Repository
    pull_request_count: int
    issue_count: int


def list_repositories(session: Session) -> list[RepositoryListItem]:
    """Return every synced repository with its open PR and issue counts.

    Counts come from two grouped aggregate queries (one per child table), then are joined
    onto the repositories in Python — so this is three queries total regardless of how many
    repositories there are, never one-count-query-per-repo. Ordered by ``full_name`` for a
    stable dashboard.
    """
    repositories = list(
        session.exec(select(Repository).order_by(col(Repository.full_name))).all()
    )

    def _counts_by_repo(model: type[PullRequest] | type[Issue]) -> dict[int, int]:
        rows = session.exec(
            select(model.repository_id, func.count()).group_by(col(model.repository_id))
        ).all()
        return {repo_id: count for repo_id, count in rows}

    pr_counts = _counts_by_repo(PullRequest)
    issue_counts = _counts_by_repo(Issue)

    return [
        RepositoryListItem(
            repository=repo,
            pull_request_count=pr_counts.get(repo.id, 0),
            issue_count=issue_counts.get(repo.id, 0),
        )
        for repo in repositories
    ]


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
