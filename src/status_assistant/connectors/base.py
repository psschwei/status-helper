"""The connector seam.

``GitHubConnector`` is the narrow interface the rest of the application depends on. It is a
``Protocol``, so any implementation (the real githubkit-backed one, or a fake in tests) is
accepted structurally — no base class to inherit. It intentionally exposes only the three
calls slice 1 needs, and every method returns *our* domain models, never a vendor type.

Adding another GitHub instance later means constructing another connector; it does not mean
changing this interface. Supporting an entirely different source (Slack, Jira) would be a
*different* protocol — this one stays GitHub-shaped on purpose.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from status_assistant.models import Issue, PullRequest, Repository


@dataclass(frozen=True)
class IssueWithAssignees:
    """An issue paired with the logins assigned to it (zero, one, or many).

    Assignees are connector-sourced data the ingestion layer needs, but an issue can have
    many of them — so they can't be a column on ``Issue``. They ride *alongside* the issue in
    this small type rather than on the model, which keeps them out of the stored row (and out
    of ``Issue.model_dump()``, so the domain object round-trips cleanly).
    """

    issue: Issue
    assignee_logins: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PullRequestWithReviewers:
    """A pull request paired with the logins requested to review it (zero, one, or many).

    Exactly the assignee story, one relationship over: a PR can have many requested reviewers,
    so they can't be a column on ``PullRequest`` and ride *alongside* it here instead. The REST
    ``pulls.list`` payload already carries ``requested_reviewers``, so this needs no extra API
    call — the connector maps it in the same pass it maps the PR.
    """

    pull_request: PullRequest
    requested_reviewer_logins: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ActivityRecord:
    """One activity event observed from GitHub, source-shaped but vendor-free.

    The connector reports *what happened* — the kind, subject, actor, and time — but not the
    ``repository_id``: it doesn't yet know the repository's numeric id (the same reason
    ``PullRequest.repository_id`` is left at ``0``). The ingestion layer owns the ``Repository``
    row, stamps the real id, and turns each record into an ``ActivityEvent`` with its
    deterministic key. Keeping this a distinct connector type (not the ``ActivityEvent`` model)
    preserves the "the connector returns domain models, never vendor types" rule without leaking
    the storage key up into the connector.

    ``review_id`` is set only for ``review_submitted`` records — GitHub's review id, which the
    ingestion layer feeds into the event key so multiple reviews by one person on one PR stay
    distinct rows.
    """

    kind: str  # matches an ActivityKind value
    subject_type: str  # "pr" | "issue"
    subject_number: int
    subject_title: str
    subject_html_url: str
    actor_login: str | None
    occurred_at: datetime
    detail: str | None = None  # review verdict, for review_submitted
    review_id: int | None = None  # GitHub review id, for review_submitted


class GitHubConnector(Protocol):
    """Read-only access to a single GitHub instance."""

    def get_repository(self, owner: str, name: str) -> Repository:
        """Fetch repository metadata."""
        ...

    def list_pull_requests(
        self, owner: str, name: str, *, state: str = "open"
    ) -> list[PullRequestWithReviewers]:
        """List pull requests for a repository (open by default), each with its requested
        reviewer logins.
        """
        ...

    def list_issues(
        self, owner: str, name: str, *, state: str = "open"
    ) -> list[IssueWithAssignees]:
        """List issues for a repository (open by default), each with its assignee logins.

        Implementations must exclude pull requests, which GitHub's issues endpoint
        otherwise returns alongside genuine issues.
        """
        ...

    def list_closing_issue_links(self, owner: str, name: str) -> list[tuple[int, int]]:
        """List a repository's open PR → issue "closes/fixes" links.

        Each pair is ``(pull_request_id, issue_id)`` using GitHub's *numeric* ids — the same
        id space as ``PullRequest.id`` and ``Issue.id`` — so the ingestion layer can match
        them against the rows it just fetched without any extra lookup. Sourced from GitHub's
        ``closingIssuesReferences`` (both closing keywords and manually-linked issues); the
        set is unfiltered here, and ingestion drops links to issues it doesn't cache.
        """
        ...

    def list_activity_since(
        self, owner: str, name: str, *, since: datetime
    ) -> list[ActivityRecord]:
        """List activity events that occurred at or after ``since``.

        Covers PR opened / merged / closed, issue opened / closed, and submitted reviews — the
        durable-history source behind the "what's happened since last scrum?" view. ``since``
        bounds how far back (and therefore how much) is fetched, so the caller controls API
        cost. Unlike the open-snapshot lists above, the returned events include items that have
        since closed or merged; the ingestion layer appends them to a log it never deletes.
        """
        ...
