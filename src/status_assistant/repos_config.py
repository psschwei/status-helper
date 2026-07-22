"""The set of repositories to watch, loaded from a TOML config file.

This is *intent* — which repositories the assistant should track — and is kept separate
from the SQLite database, which is a disposable *cache* of GitHub state. The list lives in
a committed ``repos.toml`` (no secrets: tokens stay in ``.env``), parsed with the standard
library's ``tomllib`` so there is no new dependency.

The schema is intentionally minimal (owner/name). It uses a ``[[repos]]`` array of tables,
which leaves room for a future optional per-repo ``instance`` key when multiple GitHub
instances are supported — an additive change, not a rewrite.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel


class RepoRef(BaseModel):
    """A configured repository to watch, identified by ``owner/name``."""

    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


def load_repos(path: str | Path) -> list[RepoRef]:
    """Load and validate the watched-repository list from a TOML file.

    Expects a top-level ``repos`` array of tables::

        [[repos]]
        owner = "octocat"
        name  = "hello-world"

    Raises ``FileNotFoundError`` if the file is missing and ``ValueError`` if it is present
    but malformed (not parseable, missing ``repos``, or an entry lacking owner/name) — both
    with a message pointing at the file, so a misconfiguration fails loudly at startup
    rather than silently watching nothing.
    """
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(
            f"Repository config not found at '{config_path}'. Create it (see repos.toml) "
            "with a [[repos]] entry per repository to watch."
        )

    try:
        with config_path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Could not parse repository config '{config_path}': {exc}") from exc

    raw_repos = data.get("repos")
    if not isinstance(raw_repos, list):
        raise ValueError(
            f"Repository config '{config_path}' must contain a [[repos]] array of tables."
        )

    try:
        return [RepoRef.model_validate(entry) for entry in raw_repos]
    except Exception as exc:  # pydantic ValidationError, or non-table entries
        raise ValueError(
            f"Invalid repository entry in '{config_path}': every [[repos]] needs a "
            f"string 'owner' and 'name'. ({exc})"
        ) from exc
