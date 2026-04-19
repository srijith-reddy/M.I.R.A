from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from mira.runtime.timewords import describe, parse_when


# A fixed anchor (Mon 2026-04-13 09:00 local) so "tomorrow"/"monday" resolve
# deterministically regardless of when the suite is run. All assertions below
# are derived from this anchor, not from wallclock.
_ANCHOR = datetime(2026, 4, 13, 9, 0, 0).timestamp()


def _dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts)


def test_returns_none_for_empty_or_garbage() -> None:
    assert parse_when(None) is None
    assert parse_when("") is None
    assert parse_when("   ") is None
    assert parse_when("sometime later maybe", now=_ANCHOR) is None


@pytest.mark.parametrize(
    "hint,expected_delta",
    [
        ("in 20 minutes", timedelta(minutes=20)),
        ("in 20 min", timedelta(minutes=20)),
        ("in 2 hours", timedelta(hours=2)),
        ("in 1 day", timedelta(days=1)),
        ("in 1.5 hours", timedelta(minutes=90)),
    ],
)
def test_in_N_units(hint: str, expected_delta: timedelta) -> None:
    ts = parse_when(hint, now=_ANCHOR)
    assert ts is not None
    assert _dt(ts) == _dt(_ANCHOR) + expected_delta


def test_at_3pm_today_when_before_3pm() -> None:
    ts = parse_when("at 3pm", now=_ANCHOR)
    assert ts is not None
    target = _dt(ts)
    assert target.hour == 15 and target.minute == 0
    assert target.date() == _dt(_ANCHOR).date()


def test_at_6am_rolls_to_tomorrow_when_already_past() -> None:
    # Anchor is 9am — "at 6am" must be tomorrow, not today in the past.
    ts = parse_when("at 6am", now=_ANCHOR)
    assert ts is not None
    target = _dt(ts)
    assert target.hour == 6
    assert target.date() == (_dt(_ANCHOR) + timedelta(days=1)).date()


def test_tomorrow_default_9am() -> None:
    ts = parse_when("tomorrow", now=_ANCHOR)
    assert ts is not None
    target = _dt(ts)
    assert target.hour == 9 and target.minute == 0
    assert target.date() == (_dt(_ANCHOR) + timedelta(days=1)).date()


def test_tomorrow_at_specific_time() -> None:
    ts = parse_when("tomorrow at 2:30pm", now=_ANCHOR)
    assert ts is not None
    target = _dt(ts)
    assert target.hour == 14 and target.minute == 30
    assert target.date() == (_dt(_ANCHOR) + timedelta(days=1)).date()


def test_weekday_picks_next_occurrence() -> None:
    # Anchor is Monday; "friday" should be 4 days ahead.
    ts = parse_when("friday at 10am", now=_ANCHOR)
    assert ts is not None
    target = _dt(ts)
    assert target.weekday() == 4  # Friday
    assert (target.date() - _dt(_ANCHOR).date()).days == 4


def test_weekday_same_day_rolls_to_next_week() -> None:
    # Anchor is Monday; "monday" means next Monday, not today.
    ts = parse_when("monday at 10am", now=_ANCHOR)
    assert ts is not None
    target = _dt(ts)
    assert target.weekday() == 0
    assert (target.date() - _dt(_ANCHOR).date()).days == 7


def test_iso_datetime() -> None:
    ts = parse_when("2026-04-20 14:00", now=_ANCHOR)
    assert ts is not None
    target = _dt(ts)
    assert target.year == 2026 and target.month == 4 and target.day == 20
    assert target.hour == 14


def test_describe_in_minutes() -> None:
    future = _ANCHOR + 20 * 60
    assert describe(future, now=_ANCHOR) == "in 20 minutes"


def test_describe_tomorrow() -> None:
    future = (datetime.fromtimestamp(_ANCHOR) + timedelta(days=1)).replace(
        hour=9, minute=0
    ).timestamp()
    out = describe(future, now=_ANCHOR)
    assert out.startswith("tomorrow at")


def test_describe_past_due() -> None:
    assert describe(_ANCHOR - 60, now=_ANCHOR) == "past due"
