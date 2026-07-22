"""Building engineer-summary prompts and orchestrating generation.

Two halves, kept apart on purpose:

* :func:`build_engineer_summary_prompt` is **pure and deterministic** — it turns an
  ``EngineerView`` (facts already gathered by the read-side query) into a
  :class:`SummaryPrompt`. No network, no LLM, so it is unit-testable in isolation and can be
  reused when repository/team summaries arrive.
* :func:`generate_engineer_summary` orchestrates: read the view, build the prompt, call the
  summarizer, persist the result. It is the single code path shared by the JSON API and the
  web button, so both produce and store summaries identically.

Per the product's "AI Responsibilities": deterministic code decides *what* the facts are; the
model only rewrites them into prose. We never ask it to fetch or compute.
"""

from datetime import UTC, datetime

from sqlmodel import Session

from status_assistant.ai.base import AISummarizer, SummaryPrompt
from status_assistant.models import EngineerSummary
from status_assistant.queries import EngineerView, get_engineer_view

_SYSTEM_PROMPT = (
    "You are an engineering status assistant helping a technical lead understand what an "
    "engineer is currently working on. Write a short, factual summary (2-4 sentences) of "
    "their open work, grouped naturally by theme or repository, and note anything that looks "
    "like it may be waiting or at risk. This is for engineering visibility and coordination "
    "only: it is NOT a productivity or performance measurement, so do not praise, criticize, "
    "rank, or quantify output. Use only the facts provided below; do not invent details, and "
    "if the facts are sparse, say so plainly."
)


def _format_when(value: datetime) -> str:
    """A compact UTC date for the facts block (the model doesn't need minute precision)."""
    return value.strftime("%Y-%m-%d")


def build_engineer_summary_prompt(view: EngineerView) -> SummaryPrompt:
    """Render an :class:`EngineerView` into a :class:`SummaryPrompt`.

    The user message is a plain-text fact sheet: overall counts, then per repository the
    linked issue/PR pairs, issues without a PR, and PRs without an issue — each with its
    number, title, draft flag, and last-updated date. No interpretation is baked in; that's
    the model's job.
    """
    lines: list[str] = [
        f"Engineer: {view.login}",
        (
            f"Totals: {view.pull_request_count} open pull request(s), "
            f"{view.issue_count} open issue(s) across {len(view.repos)} repositor"
            f"{'y' if len(view.repos) == 1 else 'ies'}."
        ),
        "",
    ]

    for work in view.repos:
        lines.append(f"Repository {work.repository.full_name}:")

        if work.paired:
            lines.append("  Issues with a linked pull request:")
            for pair in work.paired:
                draft = " [draft]" if pair.pull_request.is_draft else ""
                lines.append(
                    f"    - issue #{pair.issue.number} \"{pair.issue.title}\" "
                    f"closed by PR #{pair.pull_request.number} "
                    f"\"{pair.pull_request.title}\"{draft} "
                    f"(updated {_format_when(pair.issue.updated_at)})"
                )

        if work.issues_without_pr:
            lines.append("  Issues without a pull request:")
            for issue in work.issues_without_pr:
                lines.append(
                    f"    - issue #{issue.number} \"{issue.title}\" "
                    f"(updated {_format_when(issue.updated_at)})"
                )

        if work.prs_without_issue:
            lines.append("  Pull requests not linked to a tracked issue:")
            for pr in work.prs_without_issue:
                draft = " [draft]" if pr.is_draft else ""
                lines.append(
                    f"    - PR #{pr.number} \"{pr.title}\"{draft} "
                    f"(updated {_format_when(pr.updated_at)})"
                )

        lines.append("")

    return SummaryPrompt(system=_SYSTEM_PROMPT, user="\n".join(lines).rstrip())


def generate_engineer_summary(
    session: Session,
    summarizer: AISummarizer,
    model: str,
    login: str,
    allowed_logins: set[str] | None = None,
) -> EngineerSummary | None:
    """Generate, persist, and return an engineer's AI summary.

    Returns ``None`` when the login has no open work or is excluded by the roster — the same
    convention as :func:`get_engineer_view`, which this delegates the filtering to. ``model``
    is recorded on the row for provenance; it is the caller's configured model name.

    The row is upserted by login (``session.merge``), so regenerating overwrites the previous
    summary rather than accumulating history.
    """
    view = get_engineer_view(session, login, allowed_logins)
    if view is None:
        return None

    prompt = build_engineer_summary_prompt(view)
    text = summarizer.summarize(prompt)

    summary = EngineerSummary(
        login=login,
        summary_text=text,
        model=model,
        generated_at=datetime.now(UTC),
    )
    merged = session.merge(summary)
    session.commit()
    return merged
