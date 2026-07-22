"""Read-side queries against the local cache.

Kept separate from both the API and the web layer so the JSON endpoint and the HTML page
render from the *same* query, never from one calling the other over HTTP. As more views
arrive, this is where their queries live.
"""

from dataclasses import dataclass

from sqlmodel import Session, col, func, select

from status_assistant.models import (
    Issue,
    IssueAssignee,
    PullRequest,
    PullRequestIssueLink,
    Repository,
)


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
class IssuePRPair:
    """A linked issue paired with a pull request that closes it."""

    issue: Issue
    pull_request: PullRequest


@dataclass(frozen=True)
class EngineerRepoWork:
    """One engineer's open work within a single repository, split into three sections.

    The sections are ordered to tell the story of the work: an issue and the PR closing it
    first, then issues still needing a PR, then PRs not tied to any tracked issue.

    A single issue closed by two PRs (or one PR closing two issues) appears as multiple pair
    rows — the simplest faithful rendering of a many-to-many link. An issue or PR that shows
    up in ``paired`` is *not* repeated in the unpaired lists.
    """

    repository: Repository
    paired: list[IssuePRPair]
    issues_without_pr: list[Issue]
    prs_without_issue: list[PullRequest]


@dataclass(frozen=True)
class EngineerView:
    """Everything the Engineer page needs: their open work grouped per repository."""

    login: str
    repos: list[EngineerRepoWork]

    @property
    def pull_request_count(self) -> int:
        """Distinct PRs across all sections (a PR in a pair isn't double-counted)."""
        return sum(
            len({p.pull_request.id for p in r.paired} | {pr.id for pr in r.prs_without_issue})
            for r in self.repos
        )

    @property
    def issue_count(self) -> int:
        """Distinct issues across all sections (an issue in a pair isn't double-counted)."""
        return sum(
            len({p.issue.id for p in r.paired} | {i.id for i in r.issues_without_pr})
            for r in self.repos
        )


def get_engineer_view(
    session: Session, login: str, allowed_logins: set[str] | None = None
) -> EngineerView | None:
    """Return ``login``'s open PRs and issues grouped by repository, or ``None`` if none.

    "Their" PRs are the ones they *opened* (``author_login``); "their" issues are the ones
    *assigned* to them (joined through ``IssueAssignee``), consistent with the counts in
    :func:`list_engineers`. Returns ``None`` when the login has neither (the API turns that
    into a 404; the web page shows a friendly empty state) — the same convention as
    :func:`get_repository_view`.

    Work is then linked and split into three per-repository sections (see
    :class:`EngineerRepoWork`): an issue paired with the PR closing it, issues with no PR, and
    PRs with no issue. Pairing uses **union attribution** — an issue+PR pair surfaces if the
    engineer authored the PR *or* is assigned the issue, so "my PR closes someone's issue" and
    "someone's PR closes my issue" both appear. The linked counterpart is pulled in even when
    only one side is directly the engineer's. Repositories are ordered by ``full_name``;
    within each section, items are ordered most-recently-updated first.

    When ``allowed_logins`` is given and ``login`` is not in it, returns ``None`` — so an
    engineer excluded by the roster is unreachable by URL too, keeping the per-engineer page
    consistent with the filtered directory list. ``None`` (the default) means no filter.
    """
    if allowed_logins is not None and login not in allowed_logins:
        return None

    own_pull_requests = list(
        session.exec(
            select(PullRequest).where(col(PullRequest.author_login) == login)
        ).all()
    )
    own_issues = list(
        session.exec(
            select(Issue)
            .join(IssueAssignee, col(IssueAssignee.issue_id) == col(Issue.id))
            .where(col(IssueAssignee.login) == login)
        ).all()
    )

    if not own_pull_requests and not own_issues:
        return None

    # Load every link that touches one of the engineer's PRs or issues. Union attribution:
    # a link where the engineer owns *either* side brings in the counterpart, even if the
    # counterpart isn't otherwise theirs.
    own_pr_ids = {pr.id for pr in own_pull_requests}
    own_issue_ids = {i.id for i in own_issues}
    links = list(
        session.exec(
            select(PullRequestIssueLink).where(
                col(PullRequestIssueLink.pull_request_id).in_(own_pr_ids)
                | col(PullRequestIssueLink.issue_id).in_(own_issue_ids)
            )
        ).all()
    )

    # Resolve the linked counterparts we don't already hold, then index everything by id.
    linked_pr_ids = {link.pull_request_id for link in links}
    linked_issue_ids = {link.issue_id for link in links}
    prs_by_id = {pr.id: pr for pr in own_pull_requests}
    issues_by_id = {i.id: i for i in own_issues}
    missing_pr_ids = linked_pr_ids - prs_by_id.keys()
    missing_issue_ids = linked_issue_ids - issues_by_id.keys()
    if missing_pr_ids:
        for pr in session.exec(
            select(PullRequest).where(col(PullRequest.id).in_(missing_pr_ids))
        ).all():
            prs_by_id[pr.id] = pr
    if missing_issue_ids:
        for issue in session.exec(
            select(Issue).where(col(Issue.id).in_(missing_issue_ids))
        ).all():
            issues_by_id[issue.id] = issue

    # Build the pairs. Both endpoints of a stored link are always cached (ingestion enforces
    # that), but guard defensively in case one was concurrently removed.
    pairs = [
        IssuePRPair(issue=issues_by_id[link.issue_id], pull_request=prs_by_id[link.pull_request_id])
        for link in links
        if link.issue_id in issues_by_id and link.pull_request_id in prs_by_id
    ]
    paired_pr_ids = {pair.pull_request.id for pair in pairs}
    paired_issue_ids = {pair.issue.id for pair in pairs}

    # The full set of PRs/issues in view = the engineer's own plus any linked counterparts.
    all_prs = list(prs_by_id.values())
    all_issues = list(issues_by_id.values())

    # Fetch the referenced repositories in one query, then group the work in Python.
    repo_ids = {pr.repository_id for pr in all_prs} | {i.repository_id for i in all_issues}
    repositories = {
        repo.id: repo
        for repo in session.exec(
            select(Repository).where(col(Repository.id).in_(repo_ids))
        ).all()
    }

    repos: list[EngineerRepoWork] = []
    for repo_id in repo_ids:
        if repo_id not in repositories:
            continue
        repo_pairs = sorted(
            (p for p in pairs if p.issue.repository_id == repo_id),
            key=lambda p: p.issue.updated_at,
            reverse=True,
        )
        repo_issues_no_pr = sorted(
            (i for i in all_issues if i.repository_id == repo_id and i.id not in paired_issue_ids),
            key=lambda i: i.updated_at,
            reverse=True,
        )
        repo_prs_no_issue = sorted(
            (p for p in all_prs if p.repository_id == repo_id and p.id not in paired_pr_ids),
            key=lambda p: p.updated_at,
            reverse=True,
        )
        repos.append(
            EngineerRepoWork(
                repository=repositories[repo_id],
                paired=repo_pairs,
                issues_without_pr=repo_issues_no_pr,
                prs_without_issue=repo_prs_no_issue,
            )
        )
    repos.sort(key=lambda r: r.repository.full_name)

    return EngineerView(login=login, repos=repos)
