"""Domain models, which double as the SQLModel database tables.

The data here is a *cache* of GitHub state, so each row's primary key is GitHub's own
numeric id. That makes re-syncing an idempotent upsert-by-id: fetch the current state,
write it over what we had, and the table converges to what GitHub reports.

Fields are deliberately minimal — only what the Repository view needs. Labels, assignees,
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
