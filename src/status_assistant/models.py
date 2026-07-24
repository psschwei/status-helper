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
from enum import StrEnum

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


class ClosingIssueLink(SQLModel, table=True):
    """A durable, number-keyed record that a PR closes an issue — the scrum view's dedup source.

    Distinct from :class:`PullRequestIssueLink`, which is keyed by GitHub *database ids*, joins
    the open PR/Issue snapshot rows, and is wholesale-replaced every sync (so it vanishes once
    either endpoint closes). The scrum view can't use that: by the time a scrum recap runs the
    PR has merged and the issue closed, both snapshot rows are gone, and the id→number mapping
    they held is gone with them — while the :class:`ActivityEvent` log the view reads is keyed by
    *number* (``subject_number``), not id.

    So this table stores the link as ``(repository_id, pr_number, issue_number)``, joining
    directly to the activity log's ``(repository_id, subject_number)``. Like ``ActivityEvent``,
    it is **append-only**: sync never deletes it (the link is captured while the PR is still
    open, and must outlive the merge). It's bounded by the same retention sweep that prunes old
    events — ``observed_at`` (when the link was last seen) gives that sweep a cutoff column, and
    also lets a re-sync refresh the row via an idempotent upsert on the composite key.
    """

    repository_id: int = Field(foreign_key="repository.id", primary_key=True, index=True)
    pr_number: int = Field(primary_key=True)
    issue_number: int = Field(primary_key=True)
    observed_at: datetime = Field(index=True)  # when last seen (UTC); retention prunes on it


class EngineerSummary(SQLModel, table=True):
    """An AI-generated status summary for one engineer.

    Unlike the other tables, this is *derived output*, not a cache of GitHub state: it's
    prose produced by the LLM from an engineer's open work. The engineer's GitHub ``login``
    is the natural primary key — one current summary per person — so regenerating is an
    upsert-by-login (``session.merge``), overwriting the previous text (no history is kept).

    Because it isn't GitHub-cache data, a repository re-sync does **not** clear it: sync only
    replaces PR / issue / assignee / link rows (see ``ingestion/sync.py``). A summary can
    therefore go stale relative to the data it was built from; ``generated_at`` makes that
    visible in the UI.
    """

    login: str = Field(primary_key=True)
    summary_text: str
    model: str  # which LLM produced it, for provenance
    generated_at: datetime


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


class PRReviewRequest(SQLModel, table=True):
    """A GitHub login requested to review a pull request — the request, not the person.

    A PR can have zero, one, or many requested reviewers, which a single column on
    ``PullRequest`` can't represent, so requests live here as their own rows — the same
    reasoning as ``IssueAssignee``. The composite primary key ``(pull_request_id, login)``
    makes a given request unique and idempotent to re-insert.

    GitHub removes a reviewer from a PR's ``requested_reviewers`` list once they *submit* a
    review, so a row's mere presence means "still owes a review." That makes "still requested"
    a naturally-accurate proxy for "review outstanding" without our having to ingest submitted
    reviews. Only *user* reviewers are stored; team/org review requests are out of scope.
    """

    pull_request_id: int = Field(foreign_key="pullrequest.id", primary_key=True, index=True)
    login: str = Field(primary_key=True)


class ActivityKind(StrEnum):
    """The kinds of activity captured in the append-only :class:`ActivityEvent` log.

    ``PR_CLOSED`` means a pull request closed *without* being merged — a merged PR emits
    ``PR_MERGED`` instead, never both, so a timeline reads cleanly. ``PR_COMMIT`` ("worked on")
    marks commits pushed to a PR *during* the view window; it is emitted only for a PR that was
    neither opened nor merged/closed in that window (one that existed before and is still open),
    so it never doubles up with an ``PR_OPENED`` / ``PR_MERGED`` / ``PR_CLOSED`` row for the same
    PR — it fills the in-between gap those transitions leave. Values are the strings stored in the
    database (``StrEnum`` serializes to its value in TEXT and JSON alike).
    """

    PR_OPENED = "pr_opened"
    PR_MERGED = "pr_merged"
    PR_CLOSED = "pr_closed"
    PR_COMMIT = "pr_commit"
    ISSUE_OPENED = "issue_opened"
    ISSUE_CLOSED = "issue_closed"
    REVIEW_SUBMITTED = "review_submitted"


class ActivityEvent(SQLModel, table=True):
    """An append-only record of one GitHub activity event.

    This is the one table sync **never deletes**. The PR / Issue snapshot tables are a
    wholesale-replaced view of what is *currently open* (a merged PR vanishes from them); this
    log is the durable history of what *happened*, which must survive after the underlying PR
    or issue closes. That is why the subject's title / number / url are **denormalized onto the
    event** rather than joined from ``PullRequest`` / ``Issue`` — those rows are gone once the
    item closes, but the event must still render.

    Events are not 1:1 with a GitHub numeric id (one PR yields both an "opened" and a "merged"
    event; "PR opened" has no id of its own), so the primary key is a *deterministic string*
    built by :func:`build_event_key`. Re-observing the same event yields the same key, making a
    re-sync an idempotent ``session.merge`` upsert instead of a duplicate — the same reasoning
    the composite keys on ``PullRequestIssueLink`` / ``IssueAssignee`` use, expressed as one
    computed column.
    """

    id: str = Field(primary_key=True)  # deterministic key, see build_event_key
    kind: ActivityKind = Field(index=True)
    repository_id: int = Field(foreign_key="repository.id", index=True)
    # Who performed the action (PR/issue author, or the reviewer). Nullable for the same
    # deleted-"ghost"-account reason author_login is nullable elsewhere.
    actor_login: str | None = Field(default=None, index=True)
    subject_type: str  # "pr" | "issue"
    subject_number: int
    subject_title: str
    subject_html_url: str
    occurred_at: datetime = Field(index=True)  # when it happened (UTC); the query filters on it
    # Review verdict ("approved" / "changes_requested" / "commented") for REVIEW_SUBMITTED;
    # None for every other kind.
    detail: str | None = None


def build_event_key(
    repository_id: int,
    subject_type: str,
    subject_number: int,
    kind: ActivityKind,
    detail_id: int | None = None,
) -> str:
    """Build the deterministic primary key for an :class:`ActivityEvent`.

    The state-transition kinds occur at most once per subject, so
    ``(repository_id, subject_type, subject_number, kind)`` is already unique for them and
    ``detail_id`` stays ``None``. ``REVIEW_SUBMITTED`` is the exception: one reviewer can submit
    many reviews on one PR, so the caller passes GitHub's review id as ``detail_id`` to keep
    each review a distinct row — keying reviews on the actor would silently collapse them.
    """
    base = f"{repository_id}:{subject_type}:{subject_number}:{kind.value}"
    return f"{base}:{detail_id}" if detail_id is not None else base
