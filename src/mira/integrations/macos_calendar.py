from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from mira.obs.logging import log_event


@dataclass
class CalendarEvent:
    title: str
    start_ts: float  # unix seconds, local wallclock
    end_ts: float
    all_day: bool
    calendar: str
    location: str | None
    notes: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "all_day": self.all_day,
            "calendar": self.calendar,
            "location": self.location,
            "notes": self.notes,
        }


@runtime_checkable
class CalendarBackend(Protocol):
    """Minimal surface every backend must expose. Kept small on purpose —
    tests and future non-EventKit backends (ICS reader, Google Calendar API)
    only need to cover these three methods."""

    def available(self) -> bool: ...
    def ensure_access(self, *, timeout_s: float = 5.0) -> str: ...
    def fetch(self, start_ts: float, end_ts: float) -> list[CalendarEvent]: ...


class _EventKitBackend:
    """Reads events out of macOS Calendar.app via EventKit (PyObjC).

    Why EventKit and not the Google Calendar API:
      * No OAuth flow. The user already granted their accounts access to
        Calendar.app; we piggy-back on that.
      * Works with iCloud, Exchange, and anything else Calendar syncs —
        not just Google.
      * Zero keys in .env to manage.

    The framework import is lazy because PyObjC is a macOS-only dep and
    importing it on Linux CI would explode.
    """

    def __init__(self) -> None:
        self._store: Any | None = None
        self._access_status: str | None = None

    def available(self) -> bool:
        try:
            import EventKit  # noqa: F401
        except Exception:
            return False
        return True

    def _get_store(self) -> Any | None:
        if self._store is not None:
            return self._store
        try:
            from EventKit import EKEventStore  # type: ignore
        except Exception as exc:
            log_event("calendar.pyobjc_import_failed", error=repr(exc))
            return None
        self._store = EKEventStore.alloc().init()
        return self._store

    def ensure_access(self, *, timeout_s: float = 5.0) -> str:
        if self._access_status == "granted":
            return "granted"
        store = self._get_store()
        if store is None:
            return "unavailable"

        # macOS 14+ split EventKit permission into a new "full events" API;
        # on older macOS we fall back to the legacy entity-type API. The
        # callback pattern is identical between the two.
        done = threading.Event()
        captured: dict[str, Any] = {"granted": False, "error": None}

        def _completion(granted: bool, error: Any) -> None:
            captured["granted"] = bool(granted)
            captured["error"] = error
            done.set()

        req_new = getattr(store, "requestFullAccessToEventsWithCompletion_", None)
        try:
            if req_new is not None:
                req_new(_completion)
            else:
                from EventKit import EKEntityTypeEvent  # type: ignore

                store.requestAccessToEntityType_completion_(
                    EKEntityTypeEvent, _completion
                )
        except Exception as exc:
            log_event("calendar.access_request_failed", error=repr(exc))
            self._access_status = "denied"
            return "denied"

        done.wait(timeout=timeout_s)
        status = "granted" if captured["granted"] else "denied"
        self._access_status = status
        log_event(
            "calendar.access_result",
            status=status,
            error=repr(captured["error"]) if captured["error"] is not None else None,
        )
        return status

    def fetch(self, start_ts: float, end_ts: float) -> list[CalendarEvent]:
        store = self._get_store()
        if store is None:
            return []
        try:
            from Foundation import NSDate  # type: ignore
        except Exception as exc:
            log_event("calendar.foundation_import_failed", error=repr(exc))
            return []

        start = NSDate.dateWithTimeIntervalSince1970_(start_ts)
        end = NSDate.dateWithTimeIntervalSince1970_(end_ts)
        try:
            predicate = store.predicateForEventsWithStartDate_endDate_calendars_(
                start, end, None
            )
            raw = store.eventsMatchingPredicate_(predicate) or []
        except Exception as exc:
            log_event("calendar.fetch_failed", error=repr(exc))
            return []

        out: list[CalendarEvent] = []
        for ev in raw:
            try:
                s = ev.startDate()
                e = ev.endDate()
                cal_obj = ev.calendar()
                out.append(
                    CalendarEvent(
                        title=str(ev.title() or ""),
                        start_ts=float(s.timeIntervalSince1970()) if s else 0.0,
                        end_ts=float(e.timeIntervalSince1970()) if e else 0.0,
                        all_day=bool(ev.isAllDay()),
                        calendar=str(cal_obj.title() or "") if cal_obj else "",
                        location=(str(ev.location()) if ev.location() else None),
                        notes=(str(ev.notes()) if ev.notes() else None),
                    )
                )
            except Exception as exc:
                log_event("calendar.event_parse_error", error=repr(exc))
        return out


class _UnavailableBackend:
    """Used when EventKit can't be imported — non-macOS, or PyObjC missing.
    Returns clean empty results so tool calls don't explode; the caller can
    inspect `available()` to decide whether to tell the user."""

    def available(self) -> bool:
        return False

    def ensure_access(self, *, timeout_s: float = 5.0) -> str:
        return "unavailable"

    def fetch(self, start_ts: float, end_ts: float) -> list[CalendarEvent]:
        return []


_backend: CalendarBackend | None = None


def backend() -> CalendarBackend:
    """Lazy-resolved singleton. First call probes EventKit availability;
    subsequent calls reuse the chosen backend."""
    global _backend
    if _backend is None:
        candidate = _EventKitBackend()
        _backend = candidate if candidate.available() else _UnavailableBackend()
    return _backend


def set_backend(b: CalendarBackend) -> None:
    """Test hook — swap in a fake backend."""
    global _backend
    _backend = b


def reset_backend() -> None:
    """Test hook — forget the cached backend so the next `backend()` call
    re-probes. Paired with `set_backend` in tests via fixture cleanup."""
    global _backend
    _backend = None
