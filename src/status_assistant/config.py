"""Application configuration, loaded from environment variables / a `.env` file.

Split by nature: *secrets and instance connection details* live here (env / ``.env``),
while the *set of repositories to watch* lives in a separate ``repos.toml`` (see
``repos_config``) — structural config that is safe to commit. LLM settings are
intentionally absent; they arrive with the AI-summary slice.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from status_assistant.repos_config import RepoRef, load_repos


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

    # --- The repositories to watch ---
    # Path to the TOML file listing repositories (see repos_config / repos.toml). Kept out
    # of .env because it is structural config, not a secret.
    repos_config_path: str = "./repos.toml"

    # --- Persistence ---
    # SQLite file holding a cache of GitHub state; safe to delete and re-sync.
    database_url: str = "sqlite:///./status.db"

    def load_repos(self) -> list[RepoRef]:
        """Return the configured repositories to watch, read from ``repos_config_path``."""
        return load_repos(self.repos_config_path)


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance.

    Used as a FastAPI dependency so routes can depend on configuration and tests can
    override it via ``app.dependency_overrides``.
    """
    return Settings()  # values come from env / .env
