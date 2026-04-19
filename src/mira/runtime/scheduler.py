from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from mira.obs.logging import log_event
from mira.runtime.bus import bus
from mira.runtime.store import connect


_TICK_SECONDS = 15.0


@dataclass(frozen=True)
class DueReminder:
    id: int
    text: str
    fire_at: float


class ReminderScheduler:
    """Background ticker that fires `open` reminders once their `fire_at`
    arrives. Publishes `reminder.fired` on the bus with id + text; voice loop
    (or any other subscriber — menu-bar notifications, webhooks later) decides
    how to surface it.

    Why not one asyncio.sleep per reminder? Two reasons:
      * New reminders created between scheduling passes would need a wake-up
        signal; the 15-second tick gives them near-instant pickup without
        cross-thread coordination.
      * Restarts pick back up implicitly — the next tick sees everything
        with `fire_at <= now` and fires it, no on-disk "scheduled" state to
        keep consistent.

    Missed wakeups (daemon was asleep past fire_at) are fired at next tick.
    We don't try to un-miss stale reminders — firing a 2-hour-late "take out
    the trash" would be worse than silence.
    """

    # Reminders older than this at detection are considered stale and marked
    # done without firing. Prevents a week-off laptop from shouting old to-dos
    # on next boot.
    _STALE_AFTER_SECONDS = 12 * 3600

    def __init__(self, tick_seconds: float = _TICK_SECONDS) -> None:
        self._tick = tick_seconds
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.get_event_loop().create_task(self._run())
        log_event("scheduler.started", tick_seconds=self._tick)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None
        log_event("scheduler.stopped")

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick_once()
            except Exception as exc:
                # A bad tick must not kill the scheduler — log and wait.
                log_event("scheduler.tick_error", error=repr(exc))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._tick)
            except asyncio.TimeoutError:
                continue

    async def _tick_once(self) -> None:
        now = time.time()
        due, stale = self._claim_due(now)
        for r in stale:
            log_event("scheduler.skipped_stale", id=r.id, fire_at=r.fire_at)
        for r in due:
            log_event("scheduler.fired", id=r.id, text=r.text[:120])
            await bus().publish("reminder.fired", id=r.id, text=r.text, fire_at=r.fire_at)

    def _claim_due(self, now: float) -> tuple[list[DueReminder], list[DueReminder]]:
        """Atomically pick open reminders whose fire_at <= now, separate stale
        ones, and mark the firing set as done in the same transaction so two
        instances (future-proofing) can't double-fire the same row."""
        due: list[DueReminder] = []
        stale: list[DueReminder] = []
        cutoff = now - self._STALE_AFTER_SECONDS
        with connect() as conn:
            rows = conn.execute(
                "SELECT id, text, fire_at FROM reminders "
                "WHERE status='open' AND fire_at IS NOT NULL AND fire_at <= ?",
                (now,),
            ).fetchall()
            for r in rows:
                item = DueReminder(id=int(r["id"]), text=r["text"], fire_at=float(r["fire_at"]))
                if item.fire_at < cutoff:
                    stale.append(item)
                else:
                    due.append(item)
            ids_done = [x.id for x in due + stale]
            if ids_done:
                conn.executemany(
                    "UPDATE reminders SET status='done', completed_at=? "
                    "WHERE id=? AND status='open'",
                    [(now, i) for i in ids_done],
                )
        return due, stale


_scheduler: ReminderScheduler | None = None


def scheduler() -> ReminderScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = ReminderScheduler()
    return _scheduler
