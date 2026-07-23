"""githubkit-backed implementation of :class:`GitHubConnector`.

This module is the *only* place that knows about githubkit. It constructs a client for one
GitHub instance (``.com`` or an Enterprise Server, distinguished purely by ``base_url``) and
maps the vendor response objects onto our domain models. If we ever swap the client library,
this file is the blast radius.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from githubkit import GitHub

from status_assistant.connectors.base import (
    ActivityRecord,
    IssueWithAssignees,
    PullRequestWithReviewers,
)
from status_assistant.models import ActivityKind, Issue, PullRequest, Repository


def _author_login(user: Any) -> str | None:
    """Extract ``user.login`` defensively.

    GitHub can return a null author (e.g. for a deleted "ghost" account), and githubkit may
    represent an absent field with an ``UNSET`` sentinel — both are falsy here.
    """
    if not user:
        return None
    return getattr(user, "login", None)


def _assignee_logins(item: Any) -> list[str]:
    """Extract the logins of everyone assigned to an issue.

    GitHub exposes both a singular ``assignee`` and a plural ``assignees``; ``assignees`` is
    the superset (it includes the singular one), so we map from it. Null / ghost entries are
    dropped the same defensive way as :func:`_author_login`.
    """
    assignees = getattr(item, "assignees", None) or []
    return [login for user in assignees if (login := _author_login(user))]


def _requested_reviewer_logins(pr: Any) -> list[str]:
    """Extract the logins of everyone requested to review a pull request.

    ``requested_reviewers`` is the list of *user* reviewers still owing a review (GitHub drops
    a reviewer once they submit). Team review requests live under a separate
    ``requested_teams`` field and are intentionally ignored — we track people, not teams. Null
    / ghost entries are dropped the same defensive way as :func:`_author_login`.
    """
    reviewers = getattr(pr, "requested_reviewers", None) or []
    return [login for user in reviewers if (login := _author_login(user))]


def _to_datetime(value: Any) -> datetime:
    """githubkit already parses timestamps to ``datetime``; assert that invariant."""
    if not isinstance(value, datetime):  # pragma: no cover - defensive
        raise TypeError(f"expected datetime, got {type(value)!r}")
    return value


def _parse_iso(value: str) -> datetime:
    """Parse a GraphQL ISO-8601 timestamp (e.g. ``2026-06-10T00:00:00Z``) to a ``datetime``.

    Unlike the REST client, ``graphql`` returns raw JSON, so timestamps arrive as strings.
    ``fromisoformat`` accepts the trailing ``Z`` on Python 3.11+.
    """
    return datetime.fromisoformat(value)


def _to_datetime_opt(value: Any) -> datetime | None:
    """Nullable variant of :func:`_to_datetime`.

    ``merged_at`` / ``closed_at`` / ``submitted_at`` are absent (``None`` or githubkit's
    ``UNSET`` sentinel) for open PRs, un-closed issues, and pending reviews — all falsy here.
    """
    if not value:
        return None
    return _to_datetime(value)


# GitHub review states are UPPERCASE; map the three we record to our lowercase verdicts.
# PENDING (not yet submitted — null submitted_at) and DISMISSED are intentionally absent, so
# _review_verdict returns None for them and the caller skips the review.
_REVIEW_VERDICTS = {
    "APPROVED": "approved",
    "CHANGES_REQUESTED": "changes_requested",
    "COMMENTED": "commented",
}


def _review_verdict(state: Any) -> str | None:
    """Map a githubkit review ``state`` to our ``detail`` verdict, or ``None`` to skip it."""
    if not isinstance(state, str):  # pragma: no cover - defensive
        return None
    return _REVIEW_VERDICTS.get(state.upper())


class GitHubKitConnector:
    """A connector bound to a single GitHub instance.

    An "instance" is just a ``(base_url, token)`` pair. GitHub.com uses
    ``https://api.github.com``; a GitHub Enterprise Server uses ``https://<host>/api/v3``.
    """

    def __init__(self, *, base_url: str, token: str, ssl_verify: bool = True) -> None:
        self._base_url = base_url
        self._github = GitHub(token, base_url=base_url, ssl_verify=ssl_verify)

    def _paginate(self, request: Any, **kwargs: Any) -> Iterable[Any]:
        """Thin wrapper over ``GitHub.paginate``.

        githubkit's ``paginate`` is heavily generic; calling it with endpoint keyword
        arguments defeats mypy's overload resolution. We isolate that here and expose a
        plainly-typed iterable, so the mapping code below stays fully type-checked.
        """
        return self._github.paginate(request, **kwargs)

    # --- Repository ---------------------------------------------------------------

    def get_repository(self, owner: str, name: str) -> Repository:
        repo = self._github.rest.repos.get(owner=owner, repo=name).parsed_data
        return Repository(
            id=repo.id,
            owner=owner,
            name=name,
            full_name=repo.full_name,
            github_base_url=self._base_url,
            html_url=repo.html_url,
            # last_synced_at is set by the ingestion layer, not the connector.
        )

    # --- Pull requests ------------------------------------------------------------

    def list_pull_requests(
        self, owner: str, name: str, *, state: str = "open"
    ) -> list[PullRequestWithReviewers]:
        # We don't yet know the repository's numeric id here, so leave repository_id to the
        # ingestion layer, which owns the Repository row. Store 0 as a placeholder.
        prs = self._paginate(
            self._github.rest.pulls.list, owner=owner, repo=name, state=state
        )
        return [
            PullRequestWithReviewers(
                pull_request=PullRequest(
                    id=pr.id,
                    number=pr.number,
                    repository_id=0,  # filled in by ingestion
                    title=pr.title,
                    state=pr.state,
                    is_draft=bool(getattr(pr, "draft", False)),
                    author_login=_author_login(pr.user),
                    html_url=pr.html_url,
                    created_at=_to_datetime(pr.created_at),
                    updated_at=_to_datetime(pr.updated_at),
                ),
                requested_reviewer_logins=_requested_reviewer_logins(pr),
            )
            for pr in prs
        ]

    # --- Issues -------------------------------------------------------------------

    def list_issues(
        self, owner: str, name: str, *, state: str = "open"
    ) -> list[IssueWithAssignees]:
        raw = self._paginate(
            self._github.rest.issues.list_for_repo, owner=owner, repo=name, state=state
        )
        issues: list[IssueWithAssignees] = []
        for item in raw:
            # GitHub's issues endpoint also returns pull requests; a genuine issue has no
            # ``pull_request`` field. Skip the PR-flavored ones so a PR is never stored twice.
            if getattr(item, "pull_request", None):
                continue
            issue = Issue(
                id=item.id,
                number=item.number,
                repository_id=0,  # filled in by ingestion
                title=item.title,
                state=item.state,
                author_login=_author_login(item.user),
                html_url=item.html_url,
                created_at=_to_datetime(item.created_at),
                updated_at=_to_datetime(item.updated_at),
            )
            issues.append(
                IssueWithAssignees(issue=issue, assignee_logins=_assignee_logins(item))
            )
        return issues

    # --- PR → issue links ---------------------------------------------------------

    # GitHub's "linked issues" relationship (closing keywords like ``Fixes #123`` plus
    # manually-linked issues) isn't exposed by the REST pulls endpoint, so we read it via
    # GraphQL's ``closingIssuesReferences``. ``databaseId`` is the numeric REST id, matching
    # our models' primary keys. The PR list is cursor-paginated; each PR's closing references
    # are capped at 50 and *not* paginated — a single PR closing >50 issues is a mistake worth
    # noticing, not a case worth supporting.
    _CLOSING_LINKS_QUERY = """
    query($owner: String!, $name: String!, $prCursor: String) {
      repository(owner: $owner, name: $name) {
        pullRequests(states: OPEN, first: 50, after: $prCursor) {
          pageInfo { hasNextPage endCursor }
          nodes {
            databaseId
            number
            closingIssuesReferences(first: 50) {
              nodes { databaseId number }
            }
          }
        }
      }
    }
    """

    def list_closing_issue_links(self, owner: str, name: str) -> list[tuple[int, int]]:
        links: list[tuple[int, int]] = []
        pr_cursor: str | None = None
        while True:
            data = self._github.graphql(
                self._CLOSING_LINKS_QUERY,
                {"owner": owner, "name": name, "prCursor": pr_cursor},
            )
            connection = data["repository"]["pullRequests"]
            for pr in connection["nodes"]:
                pr_id = pr.get("databaseId")
                if pr_id is None:
                    continue
                for issue in pr["closingIssuesReferences"]["nodes"]:
                    issue_id = issue.get("databaseId")
                    if issue_id is not None:
                        links.append((pr_id, issue_id))
            page = connection["pageInfo"]
            if not page["hasNextPage"]:
                break
            pr_cursor = page["endCursor"]
        return links

    # Closing links keyed by *number*, for the durable scrum-view dedup. Unlike the id-keyed
    # query above (scoped to OPEN because it only feeds the open-issue ``PullRequestIssueLink``),
    # this must catch links on PRs that have already *merged* — that's the whole point, so the
    # link outlives the merge. So it walks PRs of ALL states ordered by ``UPDATED_AT`` desc, and
    # the caller stops paging once a PR predates the activity window (mirroring ``_pr_activity``).
    _CLOSING_NUMBER_LINKS_QUERY = """
    query($owner: String!, $name: String!, $prCursor: String) {
      repository(owner: $owner, name: $name) {
        pullRequests(
          first: 50, after: $prCursor,
          orderBy: {field: UPDATED_AT, direction: DESC}
        ) {
          pageInfo { hasNextPage endCursor }
          nodes {
            number
            updatedAt
            closingIssuesReferences(first: 50) {
              nodes { number }
            }
          }
        }
      }
    }
    """

    def list_closing_issue_number_links(
        self, owner: str, name: str, *, since: datetime
    ) -> list[tuple[int, int]]:
        """Closing PR→issue links keyed by *number*, for PRs updated at/after ``since``.

        Returns ``(pr_number, issue_number)`` pairs. The scrum view's dedup joins these against
        the activity log's ``subject_number``, which the id-keyed form can't reach once the
        PR/issue snapshot rows (the only id→number mapping) are gone.

        Walks PRs across *all* states newest-first by ``updatedAt`` so a link on a recently
        *merged* PR is captured — the id-keyed :meth:`list_closing_issue_links` (OPEN only)
        would miss it, leaving a merged PR and the issue it closed both showing in a scrum. Stops
        paging once a PR predates ``since``, the same early-break ``_pr_activity`` uses. A node
        missing its ``number`` is skipped.
        """
        links: list[tuple[int, int]] = []
        pr_cursor: str | None = None
        while True:
            data = self._github.graphql(
                self._CLOSING_NUMBER_LINKS_QUERY,
                {"owner": owner, "name": name, "prCursor": pr_cursor},
            )
            connection = data["repository"]["pullRequests"]
            stop = False
            for pr in connection["nodes"]:
                if _parse_iso(pr["updatedAt"]) < since:
                    stop = True  # sorted desc: this and all later PRs are outside the window
                    break
                pr_number = pr.get("number")
                if pr_number is None:
                    continue
                for issue in pr["closingIssuesReferences"]["nodes"]:
                    issue_number = issue.get("number")
                    if issue_number is not None:
                        links.append((pr_number, issue_number))
            page = connection["pageInfo"]
            if stop or not page["hasNextPage"]:
                break
            pr_cursor = page["endCursor"]
        return links

    # --- Activity feed ------------------------------------------------------------

    def list_activity_since(
        self, owner: str, name: str, *, since: datetime
    ) -> list[ActivityRecord]:
        records: list[ActivityRecord] = []
        # PRs whose reviews are worth fetching — collected while paging PRs so the (expensive)
        # per-PR review calls are bounded to PRs updated within the window. A submitted review
        # bumps its PR's updated_at, so any in-window review's PR is necessarily in this set.
        in_window_prs: list[tuple[int, int, str, str]] = []  # (id, number, title, html_url)

        records.extend(self._pr_activity(owner, name, since, in_window_prs))
        records.extend(self._issue_activity(owner, name, since))
        records.extend(self._review_activity(owner, name, since, in_window_prs))
        return records

    def _pr_activity(
        self,
        owner: str,
        name: str,
        since: datetime,
        in_window_prs: list[tuple[int, int, str, str]],
    ) -> list[ActivityRecord]:
        """Opened / merged / closed events from PRs updated since ``since``.

        ``pulls.list`` is sorted by ``updated_at`` descending, so once a PR predates the window
        every remaining PR does too — we stop paginating there rather than walking all history.
        """
        records: list[ActivityRecord] = []
        prs = self._paginate(
            self._github.rest.pulls.list,
            owner=owner,
            repo=name,
            state="all",
            sort="updated",
            direction="desc",
        )
        for pr in prs:
            if _to_datetime(pr.updated_at) < since:
                break  # sorted desc: everything after this is older than the window
            in_window_prs.append((pr.id, pr.number, pr.title, pr.html_url))

            created_at = _to_datetime(pr.created_at)
            if created_at >= since:
                records.append(
                    self._pr_record(pr, ActivityKind.PR_OPENED, created_at)
                )
            merged_at = _to_datetime_opt(getattr(pr, "merged_at", None))
            closed_at = _to_datetime_opt(getattr(pr, "closed_at", None))
            if merged_at is not None and merged_at >= since:
                # A merged PR is also "closed"; emit only PR_MERGED so it isn't double-counted.
                records.append(self._pr_record(pr, ActivityKind.PR_MERGED, merged_at))
            elif pr.state == "closed" and closed_at is not None and closed_at >= since:
                records.append(self._pr_record(pr, ActivityKind.PR_CLOSED, closed_at))
        return records

    def _pr_record(
        self, pr: Any, kind: ActivityKind, occurred_at: datetime
    ) -> ActivityRecord:
        return ActivityRecord(
            kind=kind.value,
            subject_type="pr",
            subject_number=pr.number,
            subject_title=pr.title,
            subject_html_url=pr.html_url,
            actor_login=_author_login(pr.user),
            occurred_at=occurred_at,
        )

    def _issue_activity(
        self, owner: str, name: str, since: datetime
    ) -> list[ActivityRecord]:
        """Opened / closed events from issues updated since ``since``.

        The REST ``since`` parameter filters server-side by update time, so this fetches only
        the recently-touched issues. PR-flavored items are skipped, as in :meth:`list_issues`.
        """
        records: list[ActivityRecord] = []
        raw = self._paginate(
            self._github.rest.issues.list_for_repo,
            owner=owner,
            repo=name,
            state="all",
            sort="updated",
            direction="desc",
            since=since,
        )
        for item in raw:
            if getattr(item, "pull_request", None):
                continue  # GitHub's issues endpoint also returns PRs; skip them
            created_at = _to_datetime(item.created_at)
            if created_at >= since:
                records.append(
                    self._issue_record(item, ActivityKind.ISSUE_OPENED, created_at)
                )
            closed_at = _to_datetime_opt(getattr(item, "closed_at", None))
            if item.state == "closed" and closed_at is not None and closed_at >= since:
                records.append(
                    self._issue_record(item, ActivityKind.ISSUE_CLOSED, closed_at)
                )
        return records

    def _issue_record(
        self, item: Any, kind: ActivityKind, occurred_at: datetime
    ) -> ActivityRecord:
        return ActivityRecord(
            kind=kind.value,
            subject_type="issue",
            subject_number=item.number,
            subject_title=item.title,
            subject_html_url=item.html_url,
            actor_login=_author_login(item.user),
            occurred_at=occurred_at,
        )

    def _review_activity(
        self,
        owner: str,
        name: str,
        since: datetime,
        in_window_prs: list[tuple[int, int, str, str]],
    ) -> list[ActivityRecord]:
        """Submitted-review events, one ``pulls.list_reviews`` call per in-window PR.

        This is the costliest source (a call per PR), which is why it runs only over the PRs
        already found to be updated within the window — the only PRs that can carry an in-window
        review. Pending reviews (null ``submitted_at``) and states we don't record are skipped.
        """
        records: list[ActivityRecord] = []
        for _pr_id, number, title, html_url in in_window_prs:
            reviews = self._paginate(
                self._github.rest.pulls.list_reviews,
                owner=owner,
                repo=name,
                pull_number=number,
            )
            for review in reviews:
                submitted_at = _to_datetime_opt(getattr(review, "submitted_at", None))
                if submitted_at is None or submitted_at < since:
                    continue
                verdict = _review_verdict(getattr(review, "state", None))
                if verdict is None:
                    continue  # PENDING / DISMISSED / unknown — not an event we record
                records.append(
                    ActivityRecord(
                        kind=ActivityKind.REVIEW_SUBMITTED.value,
                        subject_type="pr",
                        subject_number=number,
                        subject_title=title,
                        subject_html_url=html_url,
                        actor_login=_author_login(review.user),
                        occurred_at=submitted_at,
                        detail=verdict,
                        review_id=review.id,
                    )
                )
        return records
