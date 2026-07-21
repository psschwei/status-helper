"""The connector seam.

``GitHubConnector`` is the narrow interface the rest of the application depends on. It is a
``Protocol``, so any implementation (the real githubkit-backed one, or a fake in tests) is
accepted structurally — no base class to inherit. It intentionally exposes only the three
calls slice 1 needs, and every method returns *our* domain models, never a vendor type.

Adding another GitHub instance later means constructing another connector; it does not mean
changing this interface. Supporting an entirely different source (Slack, Jira) would be a
*different* protocol — this one stays GitHub-shaped on purpose.
"""

from typing import Protocol

from status_assistant.models import Issue, PullRequest, Repository


class GitHubConnector(Protocol):
    """Read-only access to a single GitHub instance."""

    def get_repository(self, owner: str, name: str) -> Repository:
        """Fetch repository metadata."""
        ...

    def list_pull_requests(
        self, owner: str, name: str, *, state: str = "open"
    ) -> list[PullRequest]:
        """List pull requests for a repository (open by default)."""
        ...

    def list_issues(self, owner: str, name: str, *, state: str = "open") -> list[Issue]:
        """List issues for a repository (open by default).

        Implementations must exclude pull requests, which GitHub's issues endpoint
        otherwise returns alongside genuine issues.
        """
        ...
