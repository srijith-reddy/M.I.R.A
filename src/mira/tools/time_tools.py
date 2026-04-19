from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from mira.runtime.registry import tool


class NowArgs(BaseModel):
    pass


@tool(
    "time.now",
    description=(
        "Returns the current local date and time. Call this before answering "
        "any question about the current time, today's date, or the day of "
        "the week — do not guess."
    ),
    params=NowArgs,
    tags=("time",),
)
async def time_now(_: NowArgs) -> dict[str, Any]:
    now = datetime.now().astimezone()
    return {
        "iso": now.isoformat(timespec="seconds"),
        "human": now.strftime("%A, %B %-d %Y, %-I:%M %p %Z"),
        "hour": now.hour,
        "minute": now.minute,
        "weekday": now.strftime("%A"),
        "date": now.strftime("%Y-%m-%d"),
        "tz": now.strftime("%Z"),
    }
