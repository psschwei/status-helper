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
