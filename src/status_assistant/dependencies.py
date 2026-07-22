"""Shared FastAPI dependencies.

Centralizes how the application obtains a GitHub connector. Access goes through
``get_connector_for(base_url)`` — a factory keyed by instance URL. Today every repository
uses the single instance configured in ``.env``, so the factory always builds from global
settings; keeping the *base_url* as its parameter is the seam that lets a later slice
resolve a different instance (and token) per repository without changing call sites.

The JSON API depends on ``get_connector``; tests override it via ``app.dependency_overrides``
to inject a fake, so no test ever hits GitHub.
"""

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
