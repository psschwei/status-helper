"""Shared FastAPI dependencies.

Centralizes how a request obtains a GitHub connector. Both the JSON API and (later) other
callers depend on ``get_connector``; tests override it via ``app.dependency_overrides`` to
inject a fake, so no test ever hits GitHub.
"""

from status_assistant.config import Settings, get_settings
from status_assistant.connectors.base import GitHubConnector
from status_assistant.connectors.github import GitHubKitConnector


def get_connector(settings: Settings | None = None) -> GitHubConnector:
    """Build the connector for the configured GitHub instance.

    Slice 1 has exactly one instance, so this constructs one connector from settings. When
    multiple instances arrive, this becomes the place that selects the right one per request.
    """
    settings = settings or get_settings()
    return GitHubKitConnector(
        base_url=settings.github_base_url,
        token=settings.github_token.get_secret_value(),
        ssl_verify=settings.github_ssl_verify,
    )
