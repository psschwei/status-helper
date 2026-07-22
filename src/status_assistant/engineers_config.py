"""The optional roster of engineers to show in the Engineers view, from a TOML config file.

Like ``repos_config``, this is *intent* — which people the assistant should surface — kept
separate from the SQLite cache and safe to commit (no secrets). It is parsed with the
standard library's ``tomllib`` so there is no new dependency.

An engineer is a *person*, not a handle. Today all handles live on the single GitHub
instance configured in ``.env``, so matching collapses to a flat set of handles. But one
person may have a *different handle on each instance* once multiple instances are supported,
so the schema keys on the person (a ``[[engineers]]`` entry) and reserves an optional
``handles_by_instance`` map for that future — an additive change, not a rewrite. This
mirrors ``Repository.github_base_url``, already stored so instances can be disambiguated
without a schema change.

Unlike ``repos.toml`` (required — watching nothing is pointless), this roster is *optional*:
a missing file means "no filter — show everyone", so the feature is purely opt-in.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel


class EngineerRef(BaseModel):
    """One person to include in the Engineers view, identified by their GitHub handle(s)."""

    # Optional display name. Stored now for a later slice; the view still renders the login.
    name: str | None = None
    # Handles on the default instance (the one in .env). Usually a single handle.
    handles: list[str] = []
    # Reserved for multi-instance: {github_base_url: handle}. Parsed now, but matching does
    # not yet key on instance — see ``all_handles``.
    handles_by_instance: dict[str, str] = {}

    @property
    def all_handles(self) -> set[str]:
        """Every handle this person is known by, across ``handles`` and per-instance ones.

        Instance-agnostic for now (single instance). When multiple instances land, matching
        moves to ``(github_base_url, handle)`` pairs joined on ``Repository.github_base_url``.
        """
        return set(self.handles) | set(self.handles_by_instance.values())


def load_engineers(path: str | Path) -> list[EngineerRef]:
    """Load and validate the engineer roster from a TOML file.

    Expects a top-level ``engineers`` array of tables::

        [[engineers]]
        name    = "Octo Cat"
        handles = ["octocat"]

    A **missing file yields an empty list** (no roster ⇒ no filter ⇒ show everyone) — the
    roster is opt-in, unlike ``repos.toml``. A file that is *present but malformed* (not
    parseable, ``engineers`` not an array, or an entry with no handles) raises ``ValueError``
    pointing at the file, so a real misconfiguration fails loudly rather than silently
    showing everyone.
    """
    config_path = Path(path)
    if not config_path.is_file():
        return []

    try:
        with config_path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Could not parse engineers config '{config_path}': {exc}") from exc

    raw_engineers = data.get("engineers")
    if not isinstance(raw_engineers, list):
        raise ValueError(
            f"Engineers config '{config_path}' must contain an [[engineers]] array of tables."
        )

    try:
        engineers = [EngineerRef.model_validate(entry) for entry in raw_engineers]
    except Exception as exc:  # pydantic ValidationError, or non-table entries
        raise ValueError(
            f"Invalid engineer entry in '{config_path}': {exc}"
        ) from exc

    for engineer in engineers:
        if not engineer.all_handles:
            raise ValueError(
                f"Invalid engineer entry in '{config_path}': every [[engineers]] needs at "
                "least one handle (in 'handles' or 'handles_by_instance')."
            )

    return engineers


def allowed_logins(engineers: list[EngineerRef]) -> set[str] | None:
    """Return the set of handles the Engineers view should be limited to, or ``None``.

    ``None`` is the "no filter" sentinel: an empty roster (missing/empty config) means show
    everyone. Otherwise the result is the union of every engineer's handles.
    """
    if not engineers:
        return None
    return {handle for engineer in engineers for handle in engineer.all_handles}
