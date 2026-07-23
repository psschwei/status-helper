"""Read-side queries against the local cache.

Kept separate from both the API and the web layer so the JSON endpoint and the HTML page
render from the *same* query, never from one calling the other over HTTP. As more views
arrive, this is where their queries live.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from sqlmodel import Session, col, func, select

from status_assistant.models import (
    ActivityEvent,
    ActivityKind,
    EngineerSummary,
    Issue,
    IssueAssignee,
    PRReviewRequest,
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
class ReviewerListItem:
    """An engineer with review activity, plus their two review counts, for the reviews list.

    ``reviews_owed`` is how many open PRs (opened by someone else) list them as a still-requested
    reviewer; ``awaiting_review`` is how many of their own open PRs still have any requested
    reviewer. An engineer appears if either count is non-zero.
    """

    login: str
    reviews_owed: int
    awaiting_review: int


def list_reviewers(
    session: Session, allowed_logins: set[str] | None = None
) -> list[ReviewerListItem]:
    """Return every engineer with review activity, with their two review counts.

    Like :func:`list_engineers`, a "reviewer" isn't a stored entity — it's derived from the open
    PRs and review requests we already cache. Two notions, mirroring the per-engineer reviews
    section (see :func:`get_engineer_view`):

    * **reviews owed** — the engineer is a requested reviewer on an open PR they did *not*
      author (GitHub drops a reviewer from the request list once they submit, so a live request
      means the review is still owed). Keyed by ``PRReviewRequest.login``.
    * **awaiting review** — one of the engineer's *own* open PRs still has at least one requested
      reviewer, i.e. it's blocked on someone else. Keyed by ``PullRequest.author_login``.

    Counts come from two grouped aggregates merged in Python — a fixed number of queries
    regardless of reviewer count. Logins are the union across the two; blank logins are skipped.
    Ordered by login for a stable list.

    When ``allowed_logins`` is given, only those logins are returned (the engineer roster filter,
    see :func:`list_engineers`); ``None`` (the default) means no filter.
    """

    def _reviews_owed_by_login() -> dict[str, int]:
        rows = session.exec(
            select(PRReviewRequest.login, func.count())
            .join(
                PullRequest,
                col(PullRequest.id) == col(PRReviewRequest.pull_request_id),
            )
            .where(col(PullRequest.author_login) != PRReviewRequest.login)
            .group_by(col(PRReviewRequest.login))
        ).all()
        return {login: count for login, count in rows if login}

    def _awaiting_review_by_author() -> dict[str, int]:
        # Distinct PRs (a PR with two requested reviewers must not count twice for its author).
        rows = session.exec(
            select(PullRequest.author_login, func.count(func.distinct(col(PullRequest.id))))
            .join(
                PRReviewRequest,
                col(PRReviewRequest.pull_request_id) == col(PullRequest.id),
            )
            .where(col(PullRequest.author_login).is_not(None))
            .group_by(col(PullRequest.author_login))
        ).all()
        return {login: count for login, count in rows if login}

    owed_counts = _reviews_owed_by_login()
    awaiting_counts = _awaiting_review_by_author()

    logins = owed_counts.keys() | awaiting_counts.keys()
    if allowed_logins is not None:
        logins &= allowed_logins

    return [
        ReviewerListItem(
            login=login,
            reviews_owed=owed_counts.get(login, 0),
            awaiting_review=awaiting_counts.get(login, 0),
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
class ReviewItem:
    """A pull request in a review relationship, with the repo it lives in and its reviewers.

    Used for both "reviews you owe" (a PR where the engineer is a requested reviewer) and "your
    PRs awaiting review" (the engineer's own PR that still has requested reviewers). The
    ``repository`` rides along so the template can link and label without a second lookup;
    ``requested_reviewers`` is that PR's full outstanding-reviewer set (useful on the
    awaiting-review side to show who's holding it up).
    """

    pull_request: PullRequest
    repository: Repository
    requested_reviewers: list[str]


@dataclass(frozen=True)
class EngineerView:
    """Everything the Engineer page needs: their open work grouped per repository, plus the
    two review lists (reviews they owe, and their own PRs still awaiting review).
    """

    login: str
    repos: list[EngineerRepoWork]
    reviews_owed: list[ReviewItem]
    prs_awaiting_review: list[ReviewItem]

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


def _review_items(
    session: Session,
    pull_requests: Sequence[PullRequest],
    *,
    only_with_reviewers: bool = False,
) -> list[ReviewItem]:
    """Wrap ``pull_requests`` as :class:`ReviewItem`s, attaching each PR's repository and its
    full requested-reviewer set.

    Reviewers and repositories are fetched in one grouped query each (regardless of how many
    PRs), then joined in Python — the same fixed-query-count style as the count helpers above.
    With ``only_with_reviewers`` the result is limited to PRs that still have at least one
    requested reviewer (used for "your PRs awaiting review"); otherwise every PR is returned.
    Ordered most-recently-updated first.
    """
    if not pull_requests:
        return []

    pr_ids = {pr.id for pr in pull_requests}

    reviewers_by_pr: dict[int, list[str]] = {}
    for pr_id, reviewer in session.exec(
        select(PRReviewRequest.pull_request_id, PRReviewRequest.login).where(
            col(PRReviewRequest.pull_request_id).in_(pr_ids)
        )
    ).all():
        reviewers_by_pr.setdefault(pr_id, []).append(reviewer)

    repo_ids = {pr.repository_id for pr in pull_requests}
    repositories = {
        repo.id: repo
        for repo in session.exec(
            select(Repository).where(col(Repository.id).in_(repo_ids))
        ).all()
    }

    items = [
        ReviewItem(
            pull_request=pr,
            repository=repositories[pr.repository_id],
            requested_reviewers=sorted(reviewers_by_pr.get(pr.id, [])),
        )
        for pr in pull_requests
        if pr.repository_id in repositories
        and (not only_with_reviewers or reviewers_by_pr.get(pr.id))
    ]
    items.sort(key=lambda item: item.pull_request.updated_at, reverse=True)
    return items


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

    # Reviews the engineer *owes*: open PRs where they are a requested reviewer. GitHub drops a
    # reviewer from the request list once they submit, so a row's presence means "still owed."
    # A PR authored by the engineer is excluded — you don't owe a review on your own PR (GitHub
    # won't request it, but guard regardless).
    reviews_owed = _review_items(
        session,
        list(
            session.exec(
                select(PullRequest)
                .join(
                    PRReviewRequest,
                    col(PRReviewRequest.pull_request_id) == col(PullRequest.id),
                )
                .where(
                    col(PRReviewRequest.login) == login,
                    col(PullRequest.author_login) != login,
                )
            ).all()
        ),
    )

    # An engineer with *only* reviews owed (no PRs of their own, no assigned issues) still gets
    # a page — the review work is theirs to do even if they've opened nothing.
    if not own_pull_requests and not own_issues and not reviews_owed:
        return None

    # The engineer's own open PRs that still have any requested reviewer — i.e. blocked waiting
    # on someone else's review. Built from ``own_pull_requests`` we already hold.
    awaiting = _review_items(
        session,
        own_pull_requests,
        only_with_reviewers=True,
    )

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

    return EngineerView(
        login=login,
        repos=repos,
        reviews_owed=reviews_owed,
        prs_awaiting_review=awaiting,
    )


def get_engineer_summary(session: Session, login: str) -> EngineerSummary | None:
    """Return the stored AI summary for ``login``, or ``None`` if none has been generated.

    A primary-key lookup — the engineer page uses it to render a previously-generated summary
    without re-invoking the LLM. Generation (and the upsert that persists here) lives in the
    AI service, keeping this module read-only.
    """
    return session.get(EngineerSummary, login)


# The verb shown for each activity kind, e.g. "merged PR #42". Reviews get their verb from the
# verdict in ``ActivityEvent.detail`` instead (see ``action_phrase``).
_ACTIVITY_VERBS = {
    ActivityKind.PR_OPENED: "opened",
    ActivityKind.PR_MERGED: "merged",
    ActivityKind.PR_CLOSED: "closed",
    ActivityKind.ISSUE_OPENED: "opened",
    ActivityKind.ISSUE_CLOSED: "closed",
}

_REVIEW_VERBS = {
    "approved": "approved",
    "changes_requested": "requested changes on",
    "commented": "commented on",
}


@dataclass(frozen=True)
class ActivityEventItem:
    """One activity event with the repository it happened in.

    The ``repository`` rides along so the template can link and label without a second lookup —
    the same convention as :class:`ReviewItem`. Rendering strings are precomputed here as
    properties so the template stays thin.
    """

    event: ActivityEvent
    repository: Repository

    @property
    def subject_label(self) -> str:
        """e.g. "PR #42" / "issue #7"."""
        noun = "PR" if self.event.subject_type == "pr" else "issue"
        return f"{noun} #{self.event.subject_number}"

    @property
    def action_phrase(self) -> str:
        """A human verb phrase for the event, e.g. "merged PR #42" / "approved PR #7"."""
        if self.event.kind is ActivityKind.REVIEW_SUBMITTED:
            verb = _REVIEW_VERBS.get(self.event.detail or "", "reviewed")
        else:
            verb = _ACTIVITY_VERBS.get(self.event.kind, str(self.event.kind))
        return f"{verb} {self.subject_label}"


@dataclass(frozen=True)
class AggregatedActivity:
    """One engineer's repeated identical action on one subject, collapsed into a single row.

    "Identical" means the same rendered ``action_phrase`` on the same subject — so three
    "commented on PR #7" events become one row with ``count == 3``, while "approved PR #7" (a
    different phrase) and "merged PR #42" (a different subject) stay their own rows. ``latest`` is
    the most recent time the action happened; the view sorts and displays by it (as a date). The
    subject/repository fields are lifted off the representative event so the template can link and
    label without touching the raw events.
    """

    action_phrase: str
    subject_title: str
    subject_html_url: str
    repository: Repository
    count: int
    latest: datetime


@dataclass(frozen=True)
class EngineerActivity:
    """One engineer's activity since the scrum: their login and their aggregated actions.

    ``login`` is ``None`` for the bucket of events whose actor GitHub didn't report (a deleted
    "ghost" account) — shown only when there's no roster filter, and labeled generically in the
    UI. ``activities`` are deduped (see :class:`AggregatedActivity`) and ordered newest-first by
    ``latest``. ``action_count`` (distinct rows) drives the section header.
    """

    login: str | None
    activities: list[AggregatedActivity]

    @property
    def action_count(self) -> int:
        return len(self.activities)


@dataclass(frozen=True)
class WhatsHappenedView:
    """Everything the "what's happened since last scrum?" view needs: the effective ``since``
    bound (so the page can echo it and pre-fill the override box) and the activity grouped by
    engineer.
    """

    since: datetime
    engineers: list[EngineerActivity]


def _aggregate(items: list[ActivityEventItem]) -> list[AggregatedActivity]:
    """Collapse identical (phrase, subject) actions in one engineer's event list into rows.

    ``items`` arrive newest-first. Grouping by ``(action_phrase, subject_html_url)`` merges
    repeats (e.g. several comments on one PR) into a single :class:`AggregatedActivity`, keeping
    the latest timestamp and a count. Rows are returned newest-first by ``latest``.
    """
    groups: dict[tuple[str, str], AggregatedActivity] = {}
    for item in items:
        key = (item.action_phrase, item.event.subject_html_url)
        existing = groups.get(key)
        if existing is None:
            groups[key] = AggregatedActivity(
                action_phrase=item.action_phrase,
                subject_title=item.event.subject_title,
                subject_html_url=item.event.subject_html_url,
                repository=item.repository,
                count=1,
                # items are newest-first, so the first one seen carries the latest time.
                latest=item.event.occurred_at,
            )
        else:
            groups[key] = replace(existing, count=existing.count + 1)
    return sorted(groups.values(), key=lambda a: a.latest, reverse=True)


def get_whats_happened(
    session: Session,
    since: datetime,
    allowed_logins: set[str] | None = None,
) -> WhatsHappenedView:
    """Return activity since ``since`` grouped by the engineer who did it, deduped per action.

    Each :class:`EngineerActivity` holds one person's :class:`AggregatedActivity` rows — repeated
    identical actions on the same subject (e.g. multiple comments on one PR) collapsed into a
    single counted row — ordered newest-first. Engineers are ordered by login (the null-actor
    "ghost" bucket, if any, sorts last). This is the by-engineer framing a scrum wants — "what
    has each person been up to" — rather than a flat item timeline. ``since`` is exclusive
    (``>``), so the scrum instant itself isn't counted. When ``allowed_logins`` is given, only
    events whose ``actor_login`` is in the roster are returned (null-actor events drop under the
    filter) — the same roster convention as :func:`list_engineers` / :func:`list_reviewers`.
    Repositories are fetched in one query and joined in Python (fixed query count).
    """
    statement = (
        select(ActivityEvent)
        .where(col(ActivityEvent.occurred_at) > since)
        .order_by(col(ActivityEvent.occurred_at).desc())
    )
    if allowed_logins is not None:
        statement = statement.where(col(ActivityEvent.actor_login).in_(allowed_logins))
    events = list(session.exec(statement).all())

    repo_ids = {event.repository_id for event in events}
    repositories = {
        repo.id: repo
        for repo in session.exec(
            select(Repository).where(col(Repository.id).in_(repo_ids))
        ).all()
    }

    # Bucket by actor, preserving the newest-first order within each engineer (events are
    # already sorted, so first-seen insertion keeps that order).
    by_login: dict[str | None, list[ActivityEventItem]] = {}
    for event in events:
        if event.repository_id not in repositories:
            continue  # defensive: skip an event whose repo somehow isn't cached
        item = ActivityEventItem(event=event, repository=repositories[event.repository_id])
        by_login.setdefault(event.actor_login, []).append(item)

    # Order engineers by login; the null-actor bucket (if present) sorts last. Each engineer's
    # events are deduped into aggregated rows.
    engineers = [
        EngineerActivity(login=login, activities=_aggregate(items))
        for login, items in sorted(
            by_login.items(), key=lambda kv: (kv[0] is None, kv[0] or "")
        )
    ]
    return WhatsHappenedView(since=since, engineers=engineers)
