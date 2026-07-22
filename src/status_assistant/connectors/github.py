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

from status_assistant.connectors.base import IssueWithAssignees
from status_assistant.models import Issue, PullRequest, Repository


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


def _to_datetime(value: Any) -> datetime:
    """githubkit already parses timestamps to ``datetime``; assert that invariant."""
    if not isinstance(value, datetime):  # pragma: no cover - defensive
        raise TypeError(f"expected datetime, got {type(value)!r}")
    return value


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
    ) -> list[PullRequest]:
        # We don't yet know the repository's numeric id here, so leave repository_id to the
        # ingestion layer, which owns the Repository row. Store 0 as a placeholder.
        prs = self._paginate(
            self._github.rest.pulls.list, owner=owner, repo=name, state=state
        )
        return [
            PullRequest(
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
