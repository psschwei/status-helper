"""Tests for loading the watched-repository list from a TOML config file.

Uses ``tmp_path`` to write throwaway TOML files, so nothing depends on the committed
``repos.toml``. Covers the happy path and the failure modes that should fail loudly at
startup: missing file, unparseable TOML, missing ``repos`` array, malformed entry.
"""

from pathlib import Path

import pytest

from status_assistant.repos_config import RepoRef, load_repos


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "repos.toml"
    path.write_text(content, encoding="utf-8")
    return path


def test_loads_multiple_repos(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[repos]]
        owner = "octocat"
        name  = "hello-world"

        [[repos]]
        owner = "acme"
        name  = "api"
        """,
    )

    repos = load_repos(path)

    assert repos == [
        RepoRef(owner="octocat", name="hello-world"),
        RepoRef(owner="acme", name="api"),
    ]
    assert repos[0].full_name == "octocat/hello-world"


def test_empty_repos_array_is_allowed(tmp_path: Path) -> None:
    # A config that watches nothing is valid (not yet an error); it just yields no repos.
    path = _write(tmp_path, "repos = []\n")
    assert load_repos(path) == []


def test_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Repository config not found"):
        load_repos(tmp_path / "does-not-exist.toml")


def test_unparseable_toml_raises_value_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "this is = not = valid toml")
    with pytest.raises(ValueError, match="Could not parse"):
        load_repos(path)


def test_missing_repos_array_raises_value_error(tmp_path: Path) -> None:
    path = _write(tmp_path, 'title = "no repos here"\n')
    with pytest.raises(ValueError, match="must contain a \\[\\[repos\\]\\] array"):
        load_repos(path)


def test_entry_missing_name_raises_value_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[repos]]
        owner = "octocat"
        """,
    )
    with pytest.raises(ValueError, match="Invalid repository entry"):
        load_repos(path)
