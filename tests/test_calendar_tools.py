from __future__ import annotations

from datetime import datetime, time as dt_time, timedelta
from typing import Any

import pytest

from mira.integrations.macos_calendar import (
    CalendarEvent,
    reset_backend,
    set_backend,
)

# Importing the tools module registers them — must happen after mira is
# importable (conftest has already set the MIRA_* env).
from mira.tools import calendar_tools  # noqa: F401
from mira.runtime.registry import registry


class _FakeBackend:
    """Deterministic in-memory backend used by the tool tests. Events overlap
    the query window iff their [start, end) intersects it — matching the
    real EventKit predicate semantics."""

    def __init__(
        self,
        events: list[CalendarEvent],
        *,
        available: bool = True,
        access: str = "granted",
    ) -> None:
        self._events = events
        self._available = available
        self._access = access

    def available(self) -> bool:
        return self._available

    def ensure_access(self, *, timeout_s: float = 5.0) -> str:
        return self._access

    def fetch(self, start_ts: float, end_ts: float) -> list[CalendarEvent]:
        return [e for e in self._events if e.start_ts < end_ts and e.end_ts > start_ts]


def _ev(
    title: str,
    start_ts: float,
    *,
    duration: float = 3600,
    all_day: bool = False,
    notes: str | None = None,
    cal: str = "Work",
) -> CalendarEvent:
    return CalendarEvent(
        title=title,
        start_ts=start_ts,
        end_ts=start_ts + duration,
        all_day=all_day,
        calendar=cal,
        location=None,
        notes=notes,
    )


def _today_at(hour: int, minute: int = 0) -> float:
    today = datetime.now().date()
    return datetime.combine(today, dt_time(hour, minute)).timestamp()


def _today_midnight() -> float:
    return datetime.combine(datetime.now().date(), dt_time.min).timestamp()


@pytest.fixture(autouse=True)
def _reset_backend_between_tests() -> Any:
    yield
    reset_backend()


async def _call(tool_name: str, **kwargs: Any) -> dict[str, Any]:
    spec = registry().get(tool_name)
    assert spec is not None, f"missing tool: {tool_name}"
    return await spec.fn(spec.params_model(**kwargs))


# ---------- calendar.today ----------


@pytest.mark.asyncio
async def test_today_returns_events_in_local_day_window() -> None:
    set_backend(
        _FakeBackend(
            [
                _ev("Standup", _today_at(9)),
                _ev("Dinner tomorrow", _today_at(12) + 86400),
            ]
        )
    )
    out = await _call("calendar.today")
    assert out["status"] == "ok"
    titles = [e["title"] for e in out["events"]]
    assert "Standup" in titles
    assert "Dinner tomorrow" not in titles


@pytest.mark.asyncio
async def test_today_sorts_events_by_start_time() -> None:
    set_backend(
        _FakeBackend(
            [
                _ev("Afternoon review", _today_at(15)),
                _ev("Morning standup", _today_at(9)),
                _ev("Lunch", _today_at(12)),
            ]
        )
    )
    out = await _call("calendar.today")
    titles = [e["title"] for e in out["events"]]
    assert titles == ["Morning standup", "Lunch", "Afternoon review"]


@pytest.mark.asyncio
async def test_today_can_exclude_all_day_events() -> None:
    set_backend(
        _FakeBackend(
            [
                _ev("Standup", _today_at(9)),
                _ev(
                    "Public Holiday",
                    _today_midnight(),
                    duration=86400,
                    all_day=True,
                ),
            ]
        )
    )
    out = await _call("calendar.today", include_all_day=False)
    titles = [e["title"] for e in out["events"]]
    assert titles == ["Standup"]


# ---------- calendar.upcoming ----------


@pytest.mark.asyncio
async def test_upcoming_respects_day_window() -> None:
    now_ts = datetime.now().timestamp()
    set_backend(
        _FakeBackend(
            [
                _ev("Soon", now_ts + 3600),
                _ev("Far future", now_ts + 60 * 86400),
            ]
        )
    )
    out = await _call("calendar.upcoming", days=7)
    assert out["status"] == "ok"
    assert out["days"] == 7
    titles = [e["title"] for e in out["events"]]
    assert titles == ["Soon"]


# ---------- calendar.search ----------


@pytest.mark.asyncio
async def test_search_substring_matches_title_or_notes_case_insensitive() -> None:
    now_ts = datetime.now().timestamp()
    set_backend(
        _FakeBackend(
            [
                _ev("Dentist appointment", now_ts + 3600),
                _ev("1:1", now_ts + 7200, notes="sync with Alice"),
                _ev("Team standup", now_ts + 10800),
            ]
        )
    )
    out = await _call("calendar.search", query="ALICE")
    titles = [e["title"] for e in out["events"]]
    assert titles == ["1:1"]
    assert out["query"] == "ALICE"


@pytest.mark.asyncio
async def test_search_with_no_matches_returns_empty_list_ok_status() -> None:
    now_ts = datetime.now().timestamp()
    set_backend(_FakeBackend([_ev("Standup", now_ts + 3600)]))
    out = await _call("calendar.search", query="nonexistent")
    assert out["status"] == "ok"
    assert out["count"] == 0
    assert out["events"] == []


# ---------- error gating ----------


@pytest.mark.asyncio
async def test_tool_gracefully_reports_unavailable_backend() -> None:
    set_backend(_FakeBackend([], available=False))
    out = await _call("calendar.today")
    assert out["status"] == "unavailable"
    assert out["count"] == 0
    assert out["events"] == []
    # The reason is meant to be read aloud by the LLM verbatim — it must
    # exist and mention EventKit / Calendar so the user understands.
    assert "EventKit" in out["reason"] or "Calendar" in out["reason"]


@pytest.mark.asyncio
async def test_tool_reports_denied_access_with_remediation_hint() -> None:
    set_backend(_FakeBackend([], access="denied"))
    out = await _call("calendar.upcoming", days=7)
    assert out["status"] == "denied"
    # Remediation path must point at System Settings so the user can fix it.
    assert "System Settings" in out["reason"]


# ---------- registration smoke ----------


def test_calendar_tools_are_registered_with_calendar_tag() -> None:
    names = {t.name for t in registry().list(tag="calendar")}
    assert {"calendar.today", "calendar.upcoming", "calendar.search"} <= names
