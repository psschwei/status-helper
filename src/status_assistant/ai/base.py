"""The AI-summarizer seam.

``AISummarizer`` is the narrow interface the app depends on for turning prepared facts into
prose. It is a ``Protocol`` — any implementation (the real OpenAI-compatible one, or a fake in
tests) is accepted structurally, exactly like ``GitHubConnector``.

The seam is deliberately *prose-in, prose-out*: callers build a :class:`SummaryPrompt` from
deterministic data (see ``summarize``) and get back a string. The summarizer never fetches or
computes anything — it only rewrites the facts it is handed. Swapping the LLM library or
provider means writing another implementation, not changing this interface.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SummaryPrompt:
    """A provider-neutral prompt: a system framing plus the user-facing facts."""

    system: str
    user: str


class AISummarizer(Protocol):
    """Turns a :class:`SummaryPrompt` into a natural-language summary."""

    def summarize(self, prompt: SummaryPrompt) -> str:
        """Return the model's summary of the facts in ``prompt``."""
        ...
