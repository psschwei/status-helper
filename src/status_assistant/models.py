"""Domain models, which double as the SQLModel database tables.

The data here is a *cache* of GitHub state, so each row's primary key is GitHub's own
numeric id. That makes re-syncing an idempotent upsert-by-id: fetch the current state,
write it over what we had, and the table converges to what GitHub reports.

Fields are deliberately minimal — only what the current views need. Issue assignees are
captured (in their own ``IssueAssignee`` table, since an issue can have many); labels,
reviews, milestones, and body text are added in later slices when a feature needs them.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel


class Repository(SQLModel, table=True):
    """A GitHub repository — a first-class entity, not an assumed singleton."""

    # GitHub's numeric repository id. Not auto-generated: we set it from the API payload.
    id: int = Field(primary_key=True)
    owner: str = Field(index=True)
    name: str = Field(index=True)
    full_name: str  # "owner/name"
    # Which GitHub instance this came from (e.g. https://api.github.com). Recorded now so
    # that when multiple instances are supported, owner/name collisions can be
    # disambiguated by instance without a schema change.
    github_base_url: str
    html_url: str
    last_synced_at: datetime | None = None


class PullRequest(SQLModel, table=True):
    """An open pull request belonging to a repository."""

    id: int = Field(primary_key=True)  # GitHub's numeric PR id
    number: int
    repository_id: int = Field(foreign_key="repository.id", index=True)
    title: str
    state: str  # "open" / "closed" (slice 1 only stores open)
    is_draft: bool = False
    author_login: str | None = None
    html_url: str
    created_at: datetime
    updated_at: datetime


class Issue(SQLModel, table=True):
    """An open issue belonging to a repository.

    Note: GitHub's REST ``/issues`` endpoint also returns pull requests. Those are filtered
    out during ingestion (see ``connectors/github.py``) so a PR is never stored as an issue.
    """

    id: int = Field(primary_key=True)  # GitHub's numeric issue id
    number: int
    repository_id: int = Field(foreign_key="repository.id", index=True)
    title: str
    state: str
    author_login: str | None = None
    html_url: str
    created_at: datetime
    updated_at: datetime


class PullRequestIssueLink(SQLModel, table=True):
    """A pull request's "closes/fixes" link to an issue.

    Sourced from GitHub's ``closingIssuesReferences`` (GraphQL), which captures both closing
    keywords in a PR body (``Fixes #123``) and manually-linked issues. A PR can close many
    issues and an issue can be closed by many PRs, so the relationship lives in its own table
    rather than as a column — the same reasoning as ``IssueAssignee``.

    Only links whose issue is *also cached* (open, in a watched repo) are stored (see
    ``ingestion/sync.py``), so both endpoints always resolve to a local row. The composite
    primary key ``(pull_request_id, issue_id)`` makes a given link unique and idempotent to
    re-insert.
    """

    pull_request_id: int = Field(foreign_key="pullrequest.id", primary_key=True, index=True)
    issue_id: int = Field(foreign_key="issue.id", primary_key=True, index=True)


class IssueAssignee(SQLModel, table=True):
    """A GitHub login assigned to an issue — the assignment, not the person.

    An issue can have zero, one, or many assignees, which a single column on ``Issue`` can't
    represent, so assignments live here as their own rows. The composite primary key
    ``(issue_id, login)`` makes a given assignment unique and idempotent to re-insert. Kept
    separate from ``Issue.author_login`` (who *opened* the issue): the Engineer view counts
    issues *assigned* to a person, while the Repository page still shows who opened them.
    """

    issue_id: int = Field(foreign_key="issue.id", primary_key=True, index=True)
    login: str = Field(primary_key=True)
