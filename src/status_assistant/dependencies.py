"""Shared FastAPI dependencies.

Centralizes how the application obtains a GitHub connector and an AI summarizer. Access goes
through factory functions (``get_connector_for(base_url)`` / ``get_summarizer``) rather than
direct construction, so tests can override them via ``app.dependency_overrides`` and no test
ever hits GitHub or an LLM.

For the connector, keeping ``base_url`` as a parameter is the seam that lets a later slice
resolve a different instance (and token) per repository without changing call sites; today
every repository uses the single instance configured in ``.env``.
"""

from status_assistant.ai.base import AISummarizer
from status_assistant.ai.openai_client import OpenAISummarizer
from status_assistant.config import Settings, get_settings
from status_assistant.connectors.base import GitHubConnector
from status_assistant.connectors.github import GitHubKitConnector


def get_connector_for(base_url: str, settings: Settings | None = None) -> GitHubConnector:
    """Build a connector for the GitHub instance at ``base_url``.

    Token and SSL policy come from global settings this slice (there is one instance). When
    multiple instances arrive, this is where ``base_url`` selects the matching credentials.
    """
    settings = settings or get_settings()
    return GitHubKitConnector(
        base_url=base_url,
        token=settings.github_token.get_secret_value(),
        ssl_verify=settings.github_ssl_verify,
    )


def get_connector(settings: Settings | None = None) -> GitHubConnector:
    """Build the connector for the configured (global) GitHub instance.

    Thin wrapper over :func:`get_connector_for` for the single-instance callers and the
    test dependency override.
    """
    settings = settings or get_settings()
    return get_connector_for(settings.github_base_url, settings)


def get_summarizer(settings: Settings | None = None) -> AISummarizer:
    """Build the AI summarizer from settings.

    Callers must check ``settings.llm_configured`` first (the routes turn a False into a
    user-facing "not configured" message); this raises if invoked without a key, since an
    ``OpenAISummarizer`` needs one. Tests override this via ``app.dependency_overrides`` to
    inject a fake, so no test hits a real LLM.
    """
    settings = settings or get_settings()
    if settings.llm_api_key is None:
        raise RuntimeError("LLM is not configured (set LLM_API_KEY).")
    return OpenAISummarizer(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
    )


def get_optional_summarizer(settings: Settings | None = None) -> AISummarizer | None:
    """Return a summarizer, or ``None`` when the LLM is not configured.

    This is the FastAPI-dependency-friendly form: it never raises, so routes can inject it
    and translate a ``None`` into a friendly response (JSON 503, or a web hint) rather than a
    500 during dependency resolution. Tests override :func:`get_summarizer` with a fake; this
    delegates to it when configured, so the same override flows through here.
    """
    settings = settings or get_settings()
    if not settings.llm_configured:
        return None
    return get_summarizer(settings)
