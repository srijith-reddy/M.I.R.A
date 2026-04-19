from __future__ import annotations

import time
from datetime import datetime, time as dt_time, timedelta
from typing import Any

from pydantic import BaseModel, Field

from mira.integrations.macos_calendar import CalendarEvent, backend
from mira.runtime.registry import tool


def _fmt_event_time(ev: dict[str, Any]) -> str:
    if ev.get("all_day"):
        return "all day"
    start = ev.get("start_ts")
    if not start:
        return ""
    dt = datetime.fromtimestamp(float(start))
    # "9am" / "3:30pm" — the planner will convert to TTS-friendly phrasing.
    if dt.minute == 0:
        return dt.strftime("%-I%p").lower()
    return dt.strftime("%-I:%M%p").lower()


def _summarize_events(data: Any, *, max_items: int = 8) -> str:
    """Compact string for calendar.* results. A 20-event day becomes one
    ~200-token line instead of ~4k tokens of JSON."""
    if not isinstance(data, dict):
        return str(data)
    status = data.get("status")
    if status and status != "ok":
        # Surface the permission/unavailable reason verbatim so the LLM can
        # relay it to the user instead of inventing remediation text.
        return f"calendar {status}: {data.get('reason') or 'no detail'}"
    events = data.get("events") or []
    count = data.get("count", len(events))
    if not events:
        return "0 events."
    parts: list[str] = [f"{count} event{'s' if count != 1 else ''}:"]
    for ev in events[:max_items]:
        title = (ev.get("title") or "(no title)").strip()
        when = _fmt_event_time(ev)
        location = (ev.get("location") or "").strip()
        line = f"{title} {when}".strip()
        if location:
            line += f" @ {location}"
        parts.append(line)
    if len(events) > max_items:
        parts.append(f"...+{len(events) - max_items} more")
    return " | ".join(parts)


class TodayArgs(BaseModel):
    include_all_day: bool = Field(
        default=True,
        description="Include all-day events (holidays, OOO blocks, etc).",
    )


class UpcomingArgs(BaseModel):
    days: int = Field(default=7, ge=1, le=30)
    include_all_day: bool = True


class SearchArgs(BaseModel):
    query: str = Field(
        ..., description="Case-insensitive substring match over title + notes."
    )
    days: int = Field(
        default=30,
        ge=1,
        le=365,
        description="How many days forward from now to search.",
    )


# ---------- helpers ----------


def _gate() -> dict[str, Any] | None:
    """Return an error-shaped payload when the calendar backend isn't usable.
    None means the caller may proceed.

    We bake the remediation into the `reason` so the LLM can read it aloud
    verbatim instead of making up generic advice. macOS prompts for calendar
    access the first time we request it; once denied, only System Settings
    will flip it back."""
    b = backend()
    if not b.available():
        return {
            "status": "unavailable",
            "count": 0,
            "events": [],
            "reason": (
                "Calendar integration isn't available on this machine — "
                "EventKit (macOS Calendar) could not be loaded."
            ),
        }
    status = b.ensure_access()
    if status != "granted":
        return {
            "status": status,
            "count": 0,
            "events": [],
            "reason": (
                "I don't have permission to read your calendar. Open "
                "System Settings → Privacy & Security → Calendars and "
                "grant access to MIRA."
            ),
        }
    return None


def _day_bounds(now: float) -> tuple[float, float]:
    """[today 00:00, tomorrow 00:00) in local time. EventKit events are in
    local wallclock, so we match their frame — not UTC."""
    today = datetime.fromtimestamp(now).date()
    start = datetime.combine(today, dt_time.min).timestamp()
    end = datetime.combine(today + timedelta(days=1), dt_time.min).timestamp()
    return start, end


def _serialize(
    events: list[CalendarEvent], *, include_all_day: bool
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ev in events:
        if ev.all_day and not include_all_day:
            continue
        out.append(ev.to_dict())
    out.sort(key=lambda e: e["start_ts"])
    return out


# ---------- tools ----------


@tool(
    "calendar.today",
    description=(
        "List today's events from the user's macOS Calendar. Returns every "
        "calendar the user has linked (iCloud, Google via subscription, "
        "Exchange, etc). Read-only."
    ),
    params=TodayArgs,
    tags=("calendar",),
    summarizer=_summarize_events,
)
async def calendar_today(args: TodayArgs) -> dict[str, Any]:
    gate = _gate()
    if gate is not None:
        return gate
    start, end = _day_bounds(time.time())
    events = backend().fetch(start, end)
    items = _serialize(events, include_all_day=args.include_all_day)
    return {
        "status": "ok",
        "count": len(items),
        "events": items,
        "window_start_ts": start,
        "window_end_ts": end,
    }


@tool(
    "calendar.upcoming",
    description=(
        "List events for the next N days (default 7). Useful for 'what's on "
        "my week?' kinds of questions. Read-only."
    ),
    params=UpcomingArgs,
    tags=("calendar",),
    summarizer=_summarize_events,
)
async def calendar_upcoming(args: UpcomingArgs) -> dict[str, Any]:
    gate = _gate()
    if gate is not None:
        return gate
    now = time.time()
    end = now + args.days * 86400.0
    events = backend().fetch(now, end)
    items = _serialize(events, include_all_day=args.include_all_day)
    return {
        "status": "ok",
        "count": len(items),
        "events": items,
        "days": args.days,
    }


@tool(
    "calendar.search",
    description=(
        "Search the user's calendar by query (case-insensitive substring "
        "over title + notes). Defaults to next 30 days. Read-only."
    ),
    params=SearchArgs,
    tags=("calendar",),
    summarizer=_summarize_events,
)
async def calendar_search(args: SearchArgs) -> dict[str, Any]:
    gate = _gate()
    if gate is not None:
        return gate
    now = time.time()
    end = now + args.days * 86400.0
    events = backend().fetch(now, end)

    q = args.query.lower().strip()
    matches: list[CalendarEvent] = []
    for ev in events:
        haystack = " ".join(filter(None, [ev.title, ev.notes or ""])).lower()
        if q and q in haystack:
            matches.append(ev)
    items = _serialize(matches, include_all_day=True)
    return {
        "status": "ok",
        "count": len(items),
        "events": items,
        "query": args.query,
    }
