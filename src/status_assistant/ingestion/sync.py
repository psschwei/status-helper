"""Repository ingestion: fetch from a connector, then persist.

One entry point, ``sync_repository``, triggered manually (by the sync endpoint). No
scheduler yet. The function is deliberately connector-agnostic: it depends on the
``GitHubConnector`` protocol, so tests drive it with a fake and it never touches the network.

Sync semantics: the stored PRs and issues are a snapshot of what is *currently open* on
GitHub. Rather than merge-and-reconcile, each sync **replaces** the repository's open PRs and
issues wholesale (delete the repo's existing rows, insert the freshly-fetched set). That is
idempotent by construction and, crucially, drops items that have since closed — a merged PR
correctly disappears from the active view. It's the simplest correct behavior for an
active-only cache; incremental/closed-history strategies belong to a later slice.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlmodel import Session, col, delete

from status_assistant.connectors.base import GitHubConnector
from status_assistant.models import Issue, PullRequest


@dataclass(frozen=True)
class SyncResult:
    """Summary of a completed sync, suitable for returning from the API."""

    repository_id: int
    full_name: str
    pull_requests: int
    issues: int
    last_synced_at: datetime


def sync_repository(
    session: Session, connector: GitHubConnector, owner: str, name: str
) -> SyncResult:
    """Fetch a repository's open PRs and issues via ``connector`` and persist them.

    All writes happen in one transaction. The connector returns domain models with a
    placeholder ``repository_id``; this layer owns the ``Repository`` row and stamps the real
    id onto each child before insert.
    """
    repository = connector.get_repository(owner, name)
    pull_requests = connector.list_pull_requests(owner, name, state="open")
    issues = connector.list_issues(owner, name, state="open")

    now = datetime.now(UTC)
    repository.last_synced_at = now

    # Upsert the repository row (id is GitHub's, so merge is an upsert-by-id).
    session.merge(repository)

    # Replace this repository's cached children with the freshly-fetched open set.
    # ``col()`` yields a typed column expression so the comparison is a SQL predicate,
    # not a Python bool.
    session.exec(delete(PullRequest).where(col(PullRequest.repository_id) == repository.id))
    session.exec(delete(Issue).where(col(Issue.repository_id) == repository.id))

    for pr in pull_requests:
        pr.repository_id = repository.id
        session.add(pr)
    for issue in issues:
        issue.repository_id = repository.id
        session.add(issue)

    session.commit()

    return SyncResult(
        repository_id=repository.id,
        full_name=repository.full_name,
        pull_requests=len(pull_requests),
        issues=len(issues),
        last_synced_at=now,
    )
