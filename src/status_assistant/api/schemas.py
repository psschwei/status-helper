"""API response models (DTOs).

Deliberately separate from the SQLModel tables so the wire contract is decoupled from the
storage schema — the one seam worth keeping. ``from_attributes`` lets us build these directly
from ORM objects. Internal fields (surrogate FKs, etc.) are simply omitted here.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from status_assistant.queries import (
    EngineerListItem,
    EngineerView,
    RepositoryListItem,
    RepositoryView,
)


class PullRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    number: int
    title: str
    state: str
    is_draft: bool
    author_login: str | None
    html_url: str
    created_at: datetime
    updated_at: datetime


class IssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    number: int
    title: str
    state: str
    author_login: str | None
    html_url: str
    created_at: datetime
    updated_at: datetime


class RepositoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    owner: str
    name: str
    full_name: str
    html_url: str
    last_synced_at: datetime | None


class RepositoryListItemOut(BaseModel):
    """A dashboard row: a repository plus its open-work counts."""

    repository: RepositoryOut
    pull_request_count: int
    issue_count: int

    @classmethod
    def from_item(cls, item: RepositoryListItem) -> "RepositoryListItemOut":
        return cls(
            repository=RepositoryOut.model_validate(item.repository),
            pull_request_count=item.pull_request_count,
            issue_count=item.issue_count,
        )


class RepositoryViewOut(BaseModel):
    """The Repository view payload."""

    repository: RepositoryOut
    active_pull_requests: list[PullRequestOut]
    active_issues: list[IssueOut]

    @classmethod
    def from_view(cls, view: RepositoryView) -> "RepositoryViewOut":
        return cls(
            repository=RepositoryOut.model_validate(view.repository),
            active_pull_requests=[
                PullRequestOut.model_validate(pr) for pr in view.active_pull_requests
            ],
            active_issues=[IssueOut.model_validate(i) for i in view.active_issues],
        )


class EngineerListItemOut(BaseModel):
    """A directory row: an engineer (login) plus their open-work counts."""

    login: str
    pull_request_count: int
    issue_count: int

    @classmethod
    def from_item(cls, item: EngineerListItem) -> "EngineerListItemOut":
        return cls(
            login=item.login,
            pull_request_count=item.pull_request_count,
            issue_count=item.issue_count,
        )


class EngineerRepoWorkOut(BaseModel):
    """One engineer's open work within a single repository."""

    repository: RepositoryOut
    pull_requests: list[PullRequestOut]
    issues: list[IssueOut]


class EngineerViewOut(BaseModel):
    """The Engineer view payload: open work grouped per repository, plus totals."""

    login: str
    pull_request_count: int
    issue_count: int
    repos: list[EngineerRepoWorkOut]

    @classmethod
    def from_view(cls, view: EngineerView) -> "EngineerViewOut":
        return cls(
            login=view.login,
            pull_request_count=view.pull_request_count,
            issue_count=view.issue_count,
            repos=[
                EngineerRepoWorkOut(
                    repository=RepositoryOut.model_validate(work.repository),
                    pull_requests=[
                        PullRequestOut.model_validate(pr) for pr in work.pull_requests
                    ],
                    issues=[IssueOut.model_validate(i) for i in work.issues],
                )
                for work in view.repos
            ],
        )


class SyncResultOut(BaseModel):
    """Summary returned by the sync endpoint."""

    model_config = ConfigDict(from_attributes=True)

    repository_id: int
    full_name: str
    pull_requests: int
    issues: int
    last_synced_at: datetime
