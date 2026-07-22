"""OpenAI-SDK-backed implementation of :class:`AISummarizer`.

This module is the *only* place that knows about the ``openai`` library. It targets any
OpenAI-compatible endpoint — a LiteLLM proxy (provider-agnostic) or a provider's own API —
distinguished purely by ``base_url``. If we ever swap the client library, this file is the
blast radius (the same role ``connectors/github.py`` plays for githubkit).
"""

from openai import OpenAI

from status_assistant.ai.base import SummaryPrompt


class OpenAISummarizer:
    """A summarizer bound to one OpenAI-compatible endpoint and model."""

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self._model = model
        self._client = OpenAI(base_url=base_url, api_key=api_key)

    def summarize(self, prompt: SummaryPrompt) -> str:
        """Send the prompt as a system+user chat completion and return the reply text.

        Temperature is left low-ish by default via the model; we keep the call minimal and
        deterministic in shape. A missing/empty completion is surfaced as an empty string
        rather than ``None`` so callers always store a real value.
        """
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
        )
        return response.choices[0].message.content or ""
