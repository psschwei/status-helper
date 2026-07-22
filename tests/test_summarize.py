"""Unit tests for the deterministic engineer-summary prompt builder.

No LLM and no database: ``build_engineer_summary_prompt`` turns an ``EngineerView`` (plain
dataclasses) into a ``SummaryPrompt``, so it can be checked in isolation. This is the "decide
the facts deterministically" half of the AI slice; the model only rewrites what's here.
"""

from status_assistant.ai.summarize import build_engineer_summary_prompt
from status_assistant.queries import (
    EngineerRepoWork,
    EngineerView,
    IssuePRPair,
)
from tests.conftest import make_issue, make_pull_request, make_repository


def _view() -> EngineerView:
    repo = make_repository(id=1, owner="octocat", name="hello-world",
                           full_name="octocat/hello-world")
    pair = IssuePRPair(
        issue=make_issue(201, 10, "The bug", repository_id=1),
        pull_request=make_pull_request(101, 11, "Fix the bug", repository_id=1),
    )
    lone_issue = make_issue(202, 12, "Needs triage", repository_id=1)
    draft_pr = make_pull_request(102, 13, "WIP experiment", repository_id=1, is_draft=True)
    return EngineerView(
        login="alice",
        repos=[
            EngineerRepoWork(
                repository=repo,
                paired=[pair],
                issues_without_pr=[lone_issue],
                prs_without_issue=[draft_pr],
            )
        ],
    )


def test_prompt_includes_the_facts() -> None:
    prompt = build_engineer_summary_prompt(_view())

    # The engineer, the repo, and every work item's number/title reach the user message.
    assert "alice" in prompt.user
    assert "octocat/hello-world" in prompt.user
    assert "#10" in prompt.user and "The bug" in prompt.user
    assert "#11" in prompt.user and "Fix the bug" in prompt.user
    assert "#12" in prompt.user and "Needs triage" in prompt.user
    assert "#13" in prompt.user and "WIP experiment" in prompt.user
    # The draft flag is surfaced so the model can note it.
    assert "[draft]" in prompt.user


def test_prompt_includes_totals() -> None:
    prompt = build_engineer_summary_prompt(_view())
    # 2 distinct PRs (the paired one + the draft), 2 distinct issues, 1 repo.
    assert "2 open pull request(s)" in prompt.user
    assert "2 open issue(s)" in prompt.user
    assert "1 repository" in prompt.user


def test_system_prompt_has_non_productivity_guardrail() -> None:
    prompt = build_engineer_summary_prompt(_view())
    system = prompt.system.lower()
    assert "not a productivity" in system or "not a productivity or performance" in system
    # And it must forbid inventing facts.
    assert "do not invent" in system
