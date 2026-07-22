"""Tests for loading the optional engineer roster from a TOML config file.

Uses ``tmp_path`` to write throwaway TOML files, so nothing depends on a committed
``engineers.toml``. Covers the happy path, the "missing file = no filter" opt-in behavior,
and the failure modes that should fail loudly: unparseable TOML, wrong shape, no handles.
"""

from pathlib import Path

import pytest

from status_assistant.engineers_config import (
    EngineerRef,
    allowed_logins,
    load_engineers,
)


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "engineers.toml"
    path.write_text(content, encoding="utf-8")
    return path


def test_loads_multiple_engineers(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[engineers]]
        name    = "Alice A"
        handles = ["alice"]

        [[engineers]]
        handles = ["bob", "bob-alt"]
        """,
    )

    engineers = load_engineers(path)

    assert engineers == [
        EngineerRef(name="Alice A", handles=["alice"]),
        EngineerRef(handles=["bob", "bob-alt"]),
    ]


def test_missing_file_returns_empty_list(tmp_path: Path) -> None:
    # The roster is optional: a missing file means "no filter", not an error.
    assert load_engineers(tmp_path / "does-not-exist.toml") == []


def test_unparseable_toml_raises_value_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "this is = not = valid toml")
    with pytest.raises(ValueError, match="Could not parse"):
        load_engineers(path)


def test_missing_engineers_array_raises_value_error(tmp_path: Path) -> None:
    path = _write(tmp_path, 'title = "no engineers here"\n')
    with pytest.raises(ValueError, match="must contain an \\[\\[engineers\\]\\] array"):
        load_engineers(path)


def test_entry_without_handles_raises_value_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[engineers]]
        name = "Handleless"
        """,
    )
    with pytest.raises(ValueError, match="needs at least one handle"):
        load_engineers(path)


def test_handles_by_instance_counts_as_a_handle(tmp_path: Path) -> None:
    # An engineer defined only via per-instance handles is valid (no flat `handles`).
    path = _write(
        tmp_path,
        """
        [[engineers]]
        name = "Octo Cat"
        [engineers.handles_by_instance]
        "https://ghe.example.com/api/v3" = "octo-cat"
        """,
    )

    engineers = load_engineers(path)

    assert engineers[0].all_handles == {"octo-cat"}


# --- allowed_logins ---------------------------------------------------------------


def test_allowed_logins_none_for_empty_roster() -> None:
    # Empty roster is the "no filter" sentinel.
    assert allowed_logins([]) is None


def test_allowed_logins_unions_all_handles() -> None:
    engineers = [
        EngineerRef(handles=["alice"]),
        EngineerRef(
            handles=["bob"],
            handles_by_instance={"https://ghe.example.com/api/v3": "bob-ghe"},
        ),
    ]

    assert allowed_logins(engineers) == {"alice", "bob", "bob-ghe"}
