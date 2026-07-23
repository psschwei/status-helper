"""Tests for the scrum schedule config and the "last scrum" computation.

Two concerns: loading/validating ``scrum.toml`` (with a graceful default when absent, and loud
failures when present-but-malformed), and ``last_scrum_before`` correctly finding the most
recent scheduled scrum — including handling daylight saving via ``zoneinfo`` rather than a
fixed offset.
"""

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from status_assistant.scrum_config import ScrumSchedule, last_scrum_before, load_scrum


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "scrum.toml"
    path.write_text(content, encoding="utf-8")
    return path


def test_load_scrum_defaults_when_missing(tmp_path: Path) -> None:
    # No file written — the feature works out of the box with the team's default cadence.
    schedule = load_scrum(tmp_path / "nope.toml")
    assert schedule.days == ["mon", "wed", "fri"]
    assert schedule.time == "11:00"
    assert schedule.timezone == "America/New_York"


def test_load_scrum_parses_toml(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [scrum]
        days = ["tue", "thu"]
        time = "09:30"
        timezone = "Europe/London"
        """,
    )
    schedule = load_scrum(path)
    assert schedule.days == ["tue", "thu"]
    assert schedule.hour_minute == (9, 30)
    assert schedule.weekday_numbers == {1, 3}


def test_load_scrum_rejects_bad_day(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [scrum]
        days = ["mon", "funday"]
        time = "11:00"
        timezone = "America/New_York"
        """,
    )
    with pytest.raises(ValueError, match="unknown day"):
        load_scrum(path)


def test_load_scrum_rejects_bad_time(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [scrum]
        days = ["mon"]
        time = "25:99"
        timezone = "America/New_York"
        """,
    )
    with pytest.raises(ValueError, match="not HH:MM"):
        load_scrum(path)


def test_load_scrum_rejects_bad_timezone(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [scrum]
        days = ["mon"]
        time = "11:00"
        timezone = "Mars/Olympus_Mons"
        """,
    )
    with pytest.raises(ValueError, match="unknown timezone"):
        load_scrum(path)


def test_load_scrum_rejects_missing_table(tmp_path: Path) -> None:
    path = _write(tmp_path, "title = 'not a scrum config'\n")
    with pytest.raises(ValueError, match=r"\[scrum\] table"):
        load_scrum(path)


_MWF = ScrumSchedule(days=["mon", "wed", "fri"], time="11:00", timezone="America/New_York")


def test_last_scrum_before_earlier_same_week() -> None:
    # Tuesday 2026-07-21 14:00 ET → the most recent scrum is Monday 2026-07-20 11:00 ET.
    now = datetime(2026, 7, 21, 14, 0, tzinfo=ZoneInfo("America/New_York"))
    result = last_scrum_before(_MWF, now)
    assert result == datetime(2026, 7, 20, 15, 0, tzinfo=UTC)  # 11:00 EDT == 15:00 UTC


def test_last_scrum_before_wraps_to_previous_week() -> None:
    # Monday 2026-07-20 10:00 ET is *before* that day's 11:00 scrum → previous Friday.
    now = datetime(2026, 7, 20, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    result = last_scrum_before(_MWF, now)
    assert result == datetime(2026, 7, 17, 15, 0, tzinfo=UTC)  # Fri 11:00 EDT


def test_last_scrum_before_exact_boundary_is_inclusive() -> None:
    # Exactly at the scrum time counts as that scrum (<=).
    now = datetime(2026, 7, 20, 11, 0, tzinfo=ZoneInfo("America/New_York"))
    result = last_scrum_before(_MWF, now)
    assert result == datetime(2026, 7, 20, 15, 0, tzinfo=UTC)


def test_last_scrum_before_is_dst_aware() -> None:
    # Same wall-clock scrum (11:00 ET) resolves to a DIFFERENT UTC instant across DST:
    # summer is EDT (UTC-4 → 15:00 UTC); winter is EST (UTC-5 → 16:00 UTC).
    summer = last_scrum_before(
        _MWF, datetime(2026, 7, 20, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    )
    winter = last_scrum_before(
        _MWF, datetime(2026, 1, 5, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    )
    assert summer.hour == 15  # 11:00 EDT
    assert winter.hour == 16  # 11:00 EST


def test_last_scrum_before_accepts_utc_now() -> None:
    # A UTC-aware `now` (as the routes pass) is localized correctly before the search.
    now = datetime(2026, 7, 21, 18, 0, tzinfo=UTC)  # 14:00 ET Tuesday
    result = last_scrum_before(_MWF, now)
    assert result == datetime(2026, 7, 20, 15, 0, tzinfo=UTC)
