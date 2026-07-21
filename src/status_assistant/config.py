"""Application configuration, loaded from environment variables / a `.env` file.

Kept to exactly what slice 1 needs: how to reach one GitHub instance, which single
repository to sync, and where to store the SQLite cache. LLM settings are intentionally
absent — they arrive with the AI-summary slice.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings.

    Field names map to upper-cased environment variables (e.g. ``github_token`` ->
    ``GITHUB_TOKEN``), so a ``.env`` file or real env vars populate them the same way.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- GitHub instance ---
    github_token: SecretStr
    # ``https://api.github.com`` for GitHub.com; ``https://<host>/api/v3`` for
    # GitHub Enterprise Server. This one field is what makes a connector .com-or-Enterprise.
    github_base_url: str = "https://api.github.com"
    # Only disable for a GitHub Enterprise Server presenting a self-signed certificate.
    github_ssl_verify: bool = True

    # --- The single repository to sync in this slice ---
    repo_owner: str
    repo_name: str

    # --- Persistence ---
    # SQLite file holding a cache of GitHub state; safe to delete and re-sync.
    database_url: str = "sqlite:///./status.db"


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance.

    Used as a FastAPI dependency so routes can depend on configuration and tests can
    override it via ``app.dependency_overrides``.
    """
    return Settings()  # values come from env / .env
