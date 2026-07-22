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

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlmodel import Session, col, delete, select

from status_assistant.connectors.base import GitHubConnector
from status_assistant.models import (
    Issue,
    IssueAssignee,
    PRReviewRequest,
    PullRequest,
    PullRequestIssueLink,
)
from status_assistant.repos_config import RepoRef


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
    links = connector.list_closing_issue_links(owner, name)

    now = datetime.now(UTC)
    repository.last_synced_at = now

    # Upsert the repository row (id is GitHub's, so merge is an upsert-by-id).
    session.merge(repository)

    # Replace this repository's cached children with the freshly-fetched open set.
    # ``col()`` yields a typed column expression so the comparison is a SQL predicate,
    # not a Python bool.
    #
    # Assignee, review-request, and PR→issue-link rows carry no ``repository_id``, so scope
    # their deletes to this repo via a subquery on its issue / PR ids — and run them *before*
    # the issues and PRs are deleted, while those ids still resolve.
    session.exec(
        delete(IssueAssignee).where(
            col(IssueAssignee.issue_id).in_(
                select(col(Issue.id)).where(col(Issue.repository_id) == repository.id)
            )
        )
    )
    session.exec(
        delete(PRReviewRequest).where(
            col(PRReviewRequest.pull_request_id).in_(
                select(col(PullRequest.id)).where(
                    col(PullRequest.repository_id) == repository.id
                )
            )
        )
    )
    session.exec(
        delete(PullRequestIssueLink).where(
            col(PullRequestIssueLink.pull_request_id).in_(
                select(col(PullRequest.id)).where(
                    col(PullRequest.repository_id) == repository.id
                )
            )
        )
    )
    session.exec(delete(PullRequest).where(col(PullRequest.repository_id) == repository.id))
    session.exec(delete(Issue).where(col(Issue.repository_id) == repository.id))

    for pr_item in pull_requests:
        # ``pr_item.pull_request.id`` is GitHub's own id (set by the connector), so it's known
        # here — review-request rows can be built in the same pass, no flush needed.
        pr = pr_item.pull_request
        pr.repository_id = repository.id
        session.add(pr)
        for login in pr_item.requested_reviewer_logins:
            session.add(PRReviewRequest(pull_request_id=pr.id, login=login))
    for item in issues:
        # ``item.issue.id`` is GitHub's own id (set by the connector, not autoincremented),
        # so it's known here — assignee rows can be built in the same pass, no flush needed.
        item.issue.repository_id = repository.id
        session.add(item.issue)
        for login in item.assignee_logins:
            session.add(IssueAssignee(issue_id=item.issue.id, login=login))

    # Persist PR→issue links, but only when *both* endpoints are in the open set we just
    # fetched for this repo. That enforces the "cached open issues only" scope (a link to a
    # closed or cross-repo issue is dropped) and, since both ids then reference rows added
    # above, keeps the foreign keys satisfiable. Dedupe so a repeated pair inserts once.
    pr_ids = {item.pull_request.id for item in pull_requests}
    issue_ids = {item.issue.id for item in issues}
    for pr_id, issue_id in {
        (pr_id, issue_id)
        for pr_id, issue_id in links
        if pr_id in pr_ids and issue_id in issue_ids
    }:
        session.add(PullRequestIssueLink(pull_request_id=pr_id, issue_id=issue_id))

    session.commit()

    return SyncResult(
        repository_id=repository.id,
        full_name=repository.full_name,
        pull_requests=len(pull_requests),
        issues=len(issues),
        last_synced_at=now,
    )


def sync_all(
    session: Session,
    connector: GitHubConnector,
    repos: Iterable[RepoRef],
) -> list[SyncResult]:
    """Sync every configured repository, returning one :class:`SyncResult` each.

    Each repository is synced independently via :func:`sync_repository`, which commits per
    repository — so a failure syncing one repo does not roll back the repos already
    persisted. This slice has a single GitHub instance, so all repos share one ``connector``;
    resolving a connector per repository (for multiple instances) is a later, additive change.
    """
    return [sync_repository(session, connector, repo.owner, repo.name) for repo in repos]
