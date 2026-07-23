"""The scrum schedule, loaded from a TOML config file, plus the "last scrum" computation.

Like ``repos.toml`` / ``engineers.toml``, this is committed *structural* config (no secrets):
the recurring standup schedule the "what's happened since last scrum?" view uses to compute a
default ``since`` bound. Unlike ``repos.toml`` (required — watching zero repos is pointless), a
missing ``scrum.toml`` is fine: there is an obvious correct default (Mon/Wed/Fri 11:00 ET), so
the feature works out of the box and the file only needs to exist to *change* the schedule.

Times are wall-clock in a named IANA timezone. The last-scrum computation localizes each
candidate day with ``zoneinfo``, so daylight-saving transitions are handled correctly (11:00 ET
is UTC-4 in summer, UTC-5 in winter) without any fixed-offset arithmetic.
"""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel

# Three-letter weekday names -> datetime.weekday() numbers (Monday=0 .. Sunday=6).
_WEEKDAYS = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}

# Applied when scrum.toml is absent. Matches the team's actual cadence so the view is useful
# with no config at all.
_DEFAULT_DAYS = ["mon", "wed", "fri"]
_DEFAULT_TIME = "11:00"
_DEFAULT_TIMEZONE = "America/New_York"


class ScrumSchedule(BaseModel):
    """A recurring scrum schedule: which weekdays, at what local time, in which timezone."""

    days: list[str]  # three-letter lowercased weekday names, e.g. ["mon", "wed", "fri"]
    time: str  # "HH:MM", 24-hour
    timezone: str  # IANA zone name, e.g. "America/New_York"

    @property
    def weekday_numbers(self) -> set[int]:
        """The scheduled days as ``datetime.weekday()`` numbers (Monday=0 .. Sunday=6)."""
        return {_WEEKDAYS[day] for day in self.days}

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def hour_minute(self) -> tuple[int, int]:
        parsed = datetime.strptime(self.time, "%H:%M")
        return parsed.hour, parsed.minute


def _default_schedule() -> ScrumSchedule:
    return ScrumSchedule(
        days=list(_DEFAULT_DAYS), time=_DEFAULT_TIME, timezone=_DEFAULT_TIMEZONE
    )


def _validate(schedule: ScrumSchedule, source: str) -> ScrumSchedule:
    """Fail loudly on a malformed schedule, pointing at ``source``.

    Validates the day names, the ``HH:MM`` time, and that the timezone resolves — so a typo in
    the config raises at load time with an actionable message rather than surfacing as a wrong
    ``since`` (or a crash) deep inside the view.
    """
    unknown = [day for day in schedule.days if day not in _WEEKDAYS]
    if unknown:
        raise ValueError(
            f"Invalid scrum schedule in '{source}': unknown day(s) {unknown}. "
            f"Use three-letter lowercased names ({', '.join(_WEEKDAYS)})."
        )
    try:
        datetime.strptime(schedule.time, "%H:%M")
    except ValueError as exc:
        raise ValueError(
            f"Invalid scrum schedule in '{source}': time '{schedule.time}' is not HH:MM."
        ) from exc
    try:
        ZoneInfo(schedule.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(
            f"Invalid scrum schedule in '{source}': unknown timezone '{schedule.timezone}'."
        ) from exc
    return schedule


def load_scrum(path: str | Path) -> ScrumSchedule:
    """Load the scrum schedule from a TOML file, falling back to a built-in default.

    Expects a top-level ``[scrum]`` table::

        [scrum]
        days = ["mon", "wed", "fri"]
        time = "11:00"
        timezone = "America/New_York"

    A missing file returns the default (Mon/Wed/Fri 11:00 America/New_York) — the feature is
    usable with no config. A file that *is* present but malformed (unparseable, missing
    ``[scrum]``, or a bad day / time / timezone) raises ``ValueError`` pointing at the file, so
    a misconfiguration fails loudly rather than silently using the wrong window.
    """
    config_path = Path(path)
    if not config_path.is_file():
        return _default_schedule()

    try:
        with config_path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Could not parse scrum config '{config_path}': {exc}") from exc

    raw = data.get("scrum")
    if not isinstance(raw, dict):
        raise ValueError(
            f"Scrum config '{config_path}' must contain a [scrum] table with "
            "'days', 'time', and 'timezone'."
        )

    try:
        schedule = ScrumSchedule.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError, or wrong-typed fields
        raise ValueError(
            f"Invalid [scrum] table in '{config_path}': need a list 'days', a string 'time', "
            f"and a string 'timezone'. ({exc})"
        ) from exc
    return _validate(schedule, str(config_path))


def last_scrum_before(schedule: ScrumSchedule, now: datetime) -> datetime:
    """The most recent scheduled scrum at or before ``now``, returned as a UTC datetime.

    ``now`` must be timezone-aware. The search walks backwards day by day in the schedule's
    *local* timezone and returns the latest scheduled ``(day, time)`` that is ``<= now``. Each
    candidate is localized with ``ZoneInfo``, so the UTC offset is resolved for that specific
    date — daylight saving is handled with no fixed-offset arithmetic. A week always contains at
    least one scheduled day (``days`` is non-empty and validated), so the loop always returns.
    """
    tz = schedule.tzinfo
    hour, minute = schedule.hour_minute
    now_local = now.astimezone(tz)
    for delta in range(0, 8):
        candidate_date = (now_local - timedelta(days=delta)).date()
        if candidate_date.weekday() in schedule.weekday_numbers:
            candidate = datetime(
                candidate_date.year,
                candidate_date.month,
                candidate_date.day,
                hour,
                minute,
                tzinfo=tz,
            )
            if candidate <= now_local:
                return candidate.astimezone(UTC)
    raise ValueError(  # pragma: no cover - unreachable while days is non-empty
        "No scheduled scrum day found in the past week; 'days' must be non-empty."
    )
