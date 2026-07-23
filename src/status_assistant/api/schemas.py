"""API response models (DTOs).

Deliberately separate from the SQLModel tables so the wire contract is decoupled from the
storage schema — the one seam worth keeping. ``from_attributes`` lets us build these directly
from ORM objects. Internal fields (surrogate FKs, etc.) are simply omitted here.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from status_assistant.queries import (
    AggregatedActivity,
    EngineerActivity,
    EngineerListItem,
    EngineerView,
    RepositoryListItem,
    RepositoryView,
    ReviewerListItem,
    ReviewItem,
    WhatsHappenedView,
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


class ReviewerListItemOut(BaseModel):
    """A reviews-directory row: an engineer plus their reviews-owed and awaiting-review counts."""

    login: str
    reviews_owed: int
    awaiting_review: int

    @classmethod
    def from_item(cls, item: ReviewerListItem) -> "ReviewerListItemOut":
        return cls(
            login=item.login,
            reviews_owed=item.reviews_owed,
            awaiting_review=item.awaiting_review,
        )


class IssuePRPairOut(BaseModel):
    """A linked issue paired with the PR that closes it."""

    issue: IssueOut
    pull_request: PullRequestOut


class EngineerRepoWorkOut(BaseModel):
    """One engineer's open work within a single repository, split into three sections."""

    repository: RepositoryOut
    paired: list[IssuePRPairOut]
    issues_without_pr: list[IssueOut]
    prs_without_issue: list[PullRequestOut]


class ReviewItemOut(BaseModel):
    """A PR in a review relationship: the PR, its repository, and its requested reviewers."""

    pull_request: PullRequestOut
    repository: RepositoryOut
    requested_reviewers: list[str]

    @classmethod
    def from_item(cls, item: ReviewItem) -> "ReviewItemOut":
        return cls(
            pull_request=PullRequestOut.model_validate(item.pull_request),
            repository=RepositoryOut.model_validate(item.repository),
            requested_reviewers=item.requested_reviewers,
        )


class EngineerViewOut(BaseModel):
    """The Engineer view payload: open work grouped per repository, totals, and reviews."""

    login: str
    pull_request_count: int
    issue_count: int
    repos: list[EngineerRepoWorkOut]
    reviews_owed: list[ReviewItemOut]
    prs_awaiting_review: list[ReviewItemOut]

    @classmethod
    def from_view(cls, view: EngineerView) -> "EngineerViewOut":
        return cls(
            login=view.login,
            pull_request_count=view.pull_request_count,
            issue_count=view.issue_count,
            reviews_owed=[ReviewItemOut.from_item(r) for r in view.reviews_owed],
            prs_awaiting_review=[
                ReviewItemOut.from_item(r) for r in view.prs_awaiting_review
            ],
            repos=[
                EngineerRepoWorkOut(
                    repository=RepositoryOut.model_validate(work.repository),
                    paired=[
                        IssuePRPairOut(
                            issue=IssueOut.model_validate(pair.issue),
                            pull_request=PullRequestOut.model_validate(pair.pull_request),
                        )
                        for pair in work.paired
                    ],
                    issues_without_pr=[
                        IssueOut.model_validate(i) for i in work.issues_without_pr
                    ],
                    prs_without_issue=[
                        PullRequestOut.model_validate(pr) for pr in work.prs_without_issue
                    ],
                )
                for work in view.repos
            ],
        )


class EngineerSummaryOut(BaseModel):
    """An engineer's AI-generated status summary."""

    model_config = ConfigDict(from_attributes=True)

    login: str
    summary_text: str
    model: str
    generated_at: datetime


class AggregatedActivityOut(BaseModel):
    """One deduped action row: its section, a phrase + subject, how many times, and when last."""

    group: str
    action_phrase: str
    subject_title: str
    subject_html_url: str
    repository: RepositoryOut
    count: int
    latest: datetime

    @classmethod
    def from_item(cls, item: AggregatedActivity) -> "AggregatedActivityOut":
        return cls(
            group=item.group,
            action_phrase=item.action_phrase,
            subject_title=item.subject_title,
            subject_html_url=item.subject_html_url,
            repository=RepositoryOut.model_validate(item.repository),
            count=item.count,
            latest=item.latest,
        )


class EngineerActivityOut(BaseModel):
    """One engineer's activity, split into PR / review / issue sections (each deduped)."""

    login: str | None
    prs: list[AggregatedActivityOut]
    reviews: list[AggregatedActivityOut]
    issues: list[AggregatedActivityOut]

    @classmethod
    def from_item(cls, item: EngineerActivity) -> "EngineerActivityOut":
        return cls(
            login=item.login,
            prs=[AggregatedActivityOut.from_item(a) for a in item.prs],
            reviews=[AggregatedActivityOut.from_item(a) for a in item.reviews],
            issues=[AggregatedActivityOut.from_item(a) for a in item.issues],
        )


class WhatsHappenedOut(BaseModel):
    """The "what's happened since last scrum?" payload: the effective ``since`` and the activity
    grouped by engineer.
    """

    since: datetime
    engineers: list[EngineerActivityOut]

    @classmethod
    def from_view(cls, view: WhatsHappenedView) -> "WhatsHappenedOut":
        return cls(
            since=view.since,
            engineers=[EngineerActivityOut.from_item(e) for e in view.engineers],
        )


class SyncResultOut(BaseModel):
    """Summary returned by the sync endpoint."""

    model_config = ConfigDict(from_attributes=True)

    repository_id: int
    full_name: str
    pull_requests: int
    issues: int
    events: int
    last_synced_at: datetime
