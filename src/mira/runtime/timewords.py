from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

# Minimal natural-language time parser. Intentionally narrow: handles the
# five or six patterns users reliably say to a voice agent ("in 20 minutes",
# "tomorrow at 9am", "at 3 pm"). Anything more ambiguous falls through to
# None so the caller can prompt for clarification rather than guess wrong.
#
# Why not dateparser/parsedatetime?
#   * Both are ~200ms cold-start hits and pull in heavy transitive deps.
#   * Their coverage is broader than voice UX actually needs; broad parsers
#     make silent wrong-guess failures more likely, not less.
#
# Return value is a Unix timestamp (float seconds since epoch) so callers can
# write the `fire_at` column without further conversion.


_WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

_UNIT_SECONDS: dict[str, int] = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "week": 604800, "weeks": 604800,
}


def parse_when(text: str | None, *, now: float | None = None) -> float | None:
    """Parse a natural-language time hint into a Unix timestamp.

    Returns None when the input is empty, unrecognized, or ambiguous.
    Callers treat None as "no fire time" (standalone todo)."""
    if not text:
        return None
    raw = text.strip().lower()
    if not raw:
        return None

    base = datetime.fromtimestamp(now if now is not None else time.time())

    # "in 20 minutes", "in 2 hours", "in 1d"
    m = re.fullmatch(r"in\s+(\d+(?:\.\d+)?)\s*([a-z]+)", raw)
    if m:
        qty = float(m.group(1))
        unit = m.group(2)
        seconds = _UNIT_SECONDS.get(unit)
        if seconds is not None:
            return (base + timedelta(seconds=qty * seconds)).timestamp()

    # "at 3pm", "at 9:30am", "3pm", "09:30"
    time_only = _parse_clock(raw.removeprefix("at ").strip())
    if time_only is not None:
        return _today_or_tomorrow_at(base, time_only).timestamp()

    # "tomorrow at 9am", "tomorrow 9am", "tomorrow"
    if raw.startswith("tomorrow"):
        rest = raw.removeprefix("tomorrow").removeprefix(" at").strip()
        clock = _parse_clock(rest) if rest else (9, 0)  # default tomorrow 9am
        if clock is not None:
            target = (base + timedelta(days=1)).replace(
                hour=clock[0], minute=clock[1], second=0, microsecond=0
            )
            return target.timestamp()

    # "today at 9am" / "today 9am"
    if raw.startswith("today"):
        rest = raw.removeprefix("today").removeprefix(" at").strip()
        clock = _parse_clock(rest) if rest else None
        if clock is not None:
            target = base.replace(
                hour=clock[0], minute=clock[1], second=0, microsecond=0
            )
            if target <= base:
                target += timedelta(days=1)
            return target.timestamp()

    # "monday", "next friday at 10am"
    for prefix in ("next ", ""):
        if raw.startswith(prefix):
            tail = raw.removeprefix(prefix)
            for wd_name, wd_idx in _WEEKDAYS.items():
                if tail.startswith(wd_name):
                    rest = tail[len(wd_name):].removeprefix(" at").strip()
                    clock = _parse_clock(rest) if rest else (9, 0)
                    if clock is None:
                        continue
                    days_ahead = (wd_idx - base.weekday()) % 7
                    if days_ahead == 0 or prefix == "next ":
                        days_ahead = days_ahead or 7
                    target = (base + timedelta(days=days_ahead)).replace(
                        hour=clock[0], minute=clock[1], second=0, microsecond=0
                    )
                    return target.timestamp()

    # ISO 8601 "2026-04-20T14:00" or "2026-04-20 14:00"
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).timestamp()
        except ValueError:
            continue

    return None


def _parse_clock(s: str) -> tuple[int, int] | None:
    """Parse a bare clock string like '3pm', '9:30am', '14:00'. Returns
    (hour_24, minute) or None."""
    if not s:
        return None
    s = s.strip()
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    meridiem = m.group(3)

    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return (hour, minute)


def _today_or_tomorrow_at(base: datetime, clock: tuple[int, int]) -> datetime:
    """Given a clock, produce today-at-that-clock, rolling forward to tomorrow
    if the time has already passed."""
    target = base.replace(hour=clock[0], minute=clock[1], second=0, microsecond=0)
    if target <= base:
        target += timedelta(days=1)
    return target


def describe(ts: float, *, now: float | None = None) -> str:
    """Short human-readable countdown ('in 20 min', 'tomorrow at 9:00'). Used
    in spoken confirmations so the user knows what we heard."""
    base = datetime.fromtimestamp(now if now is not None else time.time())
    target = datetime.fromtimestamp(ts)
    delta = target - base

    if delta.total_seconds() < 0:
        return "past due"
    if delta.total_seconds() < 60:
        return "in a moment"
    if delta < timedelta(hours=1):
        mins = int(delta.total_seconds() // 60)
        return f"in {mins} minute{'s' if mins != 1 else ''}"
    if delta < timedelta(days=1) and target.day == base.day:
        return f"at {target.strftime('%-I:%M %p').lower()}"
    if target.date() == (base + timedelta(days=1)).date():
        return f"tomorrow at {target.strftime('%-I:%M %p').lower()}"
    return target.strftime("%A at %-I:%M %p").lower()


def as_datetime(ts: float) -> datetime:
    return datetime.fromtimestamp(ts)


__all__ = ["parse_when", "describe", "as_datetime"]
