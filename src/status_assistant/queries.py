"""Read-side queries against the local cache.

Kept separate from both the API and the web layer so the JSON endpoint and the HTML page
render from the *same* query, never from one calling the other over HTTP. As more views
arrive, this is where their queries live.
"""

from dataclasses import dataclass

from sqlmodel import Session, col, func, select

from status_assistant.models import Issue, IssueAssignee, PullRequest, Repository


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


@dataclass(frozen=True)
class EngineerListItem:
    """An engineer (a GitHub login) plus their open-work counts, for the directory list."""

    login: str
    pull_request_count: int
    issue_count: int


def list_engineers(
    session: Session, allowed_logins: set[str] | None = None
) -> list[EngineerListItem]:
    """Return every engineer with open work, with their open PR and issue counts.

    An "engineer" is not a stored entity — it's derived from the open work we already cache.
    Two different notions of "their work": a PR is counted by who *opened* it
    (``PullRequest.author_login``), while an issue is counted by who it is *assigned* to
    (``IssueAssignee.login``) — so an issue assigned to two people counts for both, and an
    unassigned issue counts for no one. Logins are the union across the two, and blank logins
    are skipped since they can't be attributed to a person. Counts come from two grouped
    aggregates merged in Python — a fixed number of queries regardless of engineer count.
    Ordered by login for a stable list.

    When ``allowed_logins`` is given, only those logins are returned — this is the engineer
    roster filter (see ``engineers_config``). ``None`` (the default) means no filter: show
    everyone. Matching is on the flat handle set for now; when multiple GitHub instances are
    supported it becomes ``(github_base_url, login)``-aware, joining on the instance already
    recorded on ``Repository.github_base_url``.
    """

    def _pr_counts_by_author() -> dict[str, int]:
        rows = session.exec(
            select(PullRequest.author_login, func.count())
            .where(col(PullRequest.author_login).is_not(None))
            .group_by(col(PullRequest.author_login))
        ).all()
        # Guard against blank logins too; ``is_not(None)`` won't catch an empty string.
        return {login: count for login, count in rows if login}

    def _issue_counts_by_assignee() -> dict[str, int]:
        rows = session.exec(
            select(IssueAssignee.login, func.count()).group_by(col(IssueAssignee.login))
        ).all()
        return {login: count for login, count in rows if login}

    pr_counts = _pr_counts_by_author()
    issue_counts = _issue_counts_by_assignee()

    logins = pr_counts.keys() | issue_counts.keys()
    if allowed_logins is not None:
        logins &= allowed_logins

    return [
        EngineerListItem(
            login=login,
            pull_request_count=pr_counts.get(login, 0),
            issue_count=issue_counts.get(login, 0),
        )
        for login in sorted(logins)
    ]


@dataclass(frozen=True)
class EngineerRepoWork:
    """One engineer's open work within a single repository."""

    repository: Repository
    pull_requests: list[PullRequest]
    issues: list[Issue]


@dataclass(frozen=True)
class EngineerView:
    """Everything the Engineer page needs: their open work grouped per repository."""

    login: str
    repos: list[EngineerRepoWork]

    @property
    def pull_request_count(self) -> int:
        return sum(len(r.pull_requests) for r in self.repos)

    @property
    def issue_count(self) -> int:
        return sum(len(r.issues) for r in self.repos)


def get_engineer_view(
    session: Session, login: str, allowed_logins: set[str] | None = None
) -> EngineerView | None:
    """Return ``login``'s open PRs and issues grouped by repository, or ``None`` if none.

    "Their" PRs are the ones they *opened* (``author_login``); "their" issues are the ones
    *assigned* to them (joined through ``IssueAssignee``), consistent with the counts in
    :func:`list_engineers`. Returns ``None`` when the login has neither (the API turns that
    into a 404; the web page shows a friendly empty state) — the same convention as
    :func:`get_repository_view`. Repositories are ordered by ``full_name``; within a repo,
    items are ordered most-recently-updated first, matching the Repository view.

    When ``allowed_logins`` is given and ``login`` is not in it, returns ``None`` — so an
    engineer excluded by the roster is unreachable by URL too, keeping the per-engineer page
    consistent with the filtered directory list. ``None`` (the default) means no filter.
    """
    if allowed_logins is not None and login not in allowed_logins:
        return None

    pull_requests = list(
        session.exec(
            select(PullRequest)
            .where(col(PullRequest.author_login) == login)
            .order_by(col(PullRequest.updated_at).desc())
        ).all()
    )
    issues = list(
        session.exec(
            select(Issue)
            .join(IssueAssignee, col(IssueAssignee.issue_id) == col(Issue.id))
            .where(col(IssueAssignee.login) == login)
            .order_by(col(Issue.updated_at).desc())
        ).all()
    )

    if not pull_requests and not issues:
        return None

    # Fetch the referenced repositories in one query, then group the work in Python.
    repo_ids = {pr.repository_id for pr in pull_requests} | {i.repository_id for i in issues}
    repositories = {
        repo.id: repo
        for repo in session.exec(
            select(Repository).where(col(Repository.id).in_(repo_ids))
        ).all()
    }

    prs_by_repo: dict[int, list[PullRequest]] = {}
    for pr in pull_requests:
        prs_by_repo.setdefault(pr.repository_id, []).append(pr)
    issues_by_repo: dict[int, list[Issue]] = {}
    for issue in issues:
        issues_by_repo.setdefault(issue.repository_id, []).append(issue)

    repos = [
        EngineerRepoWork(
            repository=repositories[repo_id],
            pull_requests=prs_by_repo.get(repo_id, []),
            issues=issues_by_repo.get(repo_id, []),
        )
        for repo_id in repo_ids
        if repo_id in repositories
    ]
    repos.sort(key=lambda r: r.repository.full_name)

    return EngineerView(login=login, repos=repos)
