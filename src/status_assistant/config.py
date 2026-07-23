"""Application configuration, loaded from environment variables / a `.env` file.

Split by nature: *secrets and instance connection details* live here (env / ``.env``),
while the *set of repositories to watch* lives in a separate ``repos.toml`` (see
``repos_config``) — structural config that is safe to commit. The LLM connection (a
LiteLLM proxy or any OpenAI-compatible endpoint) is configured here too, since its
base URL and key are instance/secret details of the same kind as the GitHub ones.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from status_assistant.engineers_config import EngineerRef, load_engineers
from status_assistant.repos_config import RepoRef, load_repos
from status_assistant.scrum_config import ScrumSchedule, load_scrum


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

    # --- The engineers to show (optional roster) ---
    # Path to the TOML file listing which engineers appear in the Engineers view (see
    # engineers_config / engineers.toml). Optional: a missing file means "show everyone".
    # Kept out of .env because it is structural config, not a secret.
    engineers_config_path: str = "./engineers.toml"

    # --- The scrum schedule (for the "what's happened since last scrum?" view) ---
    # Path to the TOML file with the recurring scrum schedule (see scrum_config / scrum.toml).
    # Optional: a missing file means the built-in default (Mon/Wed/Fri 11:00 America/New_York).
    # Kept out of .env because it is structural config, not a secret.
    scrum_config_path: str = "./scrum.toml"

    # --- LLM (AI summaries) ---
    # An OpenAI-compatible endpoint — a LiteLLM proxy in front of any provider, or a
    # provider's own API. Summaries are optional: without ``llm_api_key`` the feature is
    # disabled and the UI shows a "not configured" hint instead of the generate button.
    llm_base_url: str = "http://localhost:4000"
    llm_api_key: SecretStr | None = None
    llm_model: str = "gpt-4o-mini"

    # --- Persistence ---
    # SQLite file holding a cache of GitHub state; safe to delete and re-sync.
    database_url: str = "sqlite:///./status.db"

    @property
    def llm_configured(self) -> bool:
        """Whether AI summaries can be generated (an API key is present)."""
        return self.llm_api_key is not None

    def load_repos(self) -> list[RepoRef]:
        """Return the configured repositories to watch, read from ``repos_config_path``."""
        return load_repos(self.repos_config_path)

    def load_engineers(self) -> list[EngineerRef]:
        """Return the engineer roster, read from ``engineers_config_path`` (``[]`` if none)."""
        return load_engineers(self.engineers_config_path)

    def load_scrum(self) -> ScrumSchedule:
        """Return the scrum schedule, read from ``scrum_config_path`` (default if none)."""
        return load_scrum(self.scrum_config_path)


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance.

    Used as a FastAPI dependency so routes can depend on configuration and tests can
    override it via ``app.dependency_overrides``.
    """
    return Settings()  # values come from env / .env
