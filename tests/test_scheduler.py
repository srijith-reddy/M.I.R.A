from __future__ import annotations

import time
from typing import Any

import pytest

from mira.runtime.bus import bus
from mira.runtime.scheduler import ReminderScheduler
from mira.runtime.store import connect


def _insert_reminder(text: str, *, fire_at: float | None, status: str = "open") -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO reminders (text, when_hint, fire_at, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (text, None, fire_at, status, time.time()),
        )
        return int(cur.lastrowid or 0)


def _reminder_row(rid: int) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            "SELECT status, completed_at FROM reminders WHERE id = ?", (rid,)
        ).fetchone()
    return {"status": row["status"], "completed_at": row["completed_at"]}


@pytest.mark.asyncio
async def test_fires_and_marks_done() -> None:
    fired: list[dict[str, Any]] = []

    async def _on_fired(_topic: str, payload: dict[str, Any]) -> None:
        fired.append(payload)

    unsub = bus().subscribe("reminder.fired", _on_fired)
    try:
        rid = _insert_reminder("test due now", fire_at=time.time() - 5)
        await ReminderScheduler()._tick_once()
    finally:
        unsub()

    assert any(p.get("id") == rid for p in fired)
    row = _reminder_row(rid)
    assert row["status"] == "done"
    assert row["completed_at"] is not None


@pytest.mark.asyncio
async def test_future_reminders_are_not_fired() -> None:
    fired: list[dict[str, Any]] = []

    async def _on_fired(_topic: str, payload: dict[str, Any]) -> None:
        fired.append(payload)

    unsub = bus().subscribe("reminder.fired", _on_fired)
    try:
        rid = _insert_reminder("future", fire_at=time.time() + 3600)
        await ReminderScheduler()._tick_once()
    finally:
        unsub()

    assert not any(p.get("id") == rid for p in fired)
    assert _reminder_row(rid)["status"] == "open"


@pytest.mark.asyncio
async def test_stale_reminders_are_silenced_not_spoken() -> None:
    fired: list[dict[str, Any]] = []

    async def _on_fired(_topic: str, payload: dict[str, Any]) -> None:
        fired.append(payload)

    unsub = bus().subscribe("reminder.fired", _on_fired)
    try:
        # 48h late → older than _STALE_AFTER_SECONDS (12h). Should be marked
        # done without a fire event.
        stale_at = time.time() - 48 * 3600
        rid = _insert_reminder("old stale item", fire_at=stale_at)
        await ReminderScheduler()._tick_once()
    finally:
        unsub()

    assert not any(p.get("id") == rid for p in fired)
    assert _reminder_row(rid)["status"] == "done"


@pytest.mark.asyncio
async def test_done_reminders_ignored() -> None:
    fired: list[dict[str, Any]] = []

    async def _on_fired(_topic: str, payload: dict[str, Any]) -> None:
        fired.append(payload)

    unsub = bus().subscribe("reminder.fired", _on_fired)
    try:
        # Already-done row, even with a past fire_at, must not re-fire.
        rid = _insert_reminder(
            "previously fired", fire_at=time.time() - 60, status="done"
        )
        await ReminderScheduler()._tick_once()
    finally:
        unsub()

    assert not any(p.get("id") == rid for p in fired)


@pytest.mark.asyncio
async def test_null_fire_at_never_fires() -> None:
    fired: list[dict[str, Any]] = []

    async def _on_fired(_topic: str, payload: dict[str, Any]) -> None:
        fired.append(payload)

    unsub = bus().subscribe("reminder.fired", _on_fired)
    try:
        rid = _insert_reminder("standalone todo", fire_at=None)
        await ReminderScheduler()._tick_once()
    finally:
        unsub()

    assert not any(p.get("id") == rid for p in fired)
    assert _reminder_row(rid)["status"] == "open"
