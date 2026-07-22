"""AI services: summarization over already-gathered, deterministic facts.

The seam here mirrors ``connectors``: :class:`~status_assistant.ai.base.AISummarizer` is the
narrow protocol the app depends on, and the concrete implementation
(:class:`~status_assistant.ai.openai_client.OpenAISummarizer`) is the only place that knows
about the LLM SDK. Deterministic code shapes the facts (see ``summarize``); the model only
turns them into prose.
"""
