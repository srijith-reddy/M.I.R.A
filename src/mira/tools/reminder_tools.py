from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from mira.runtime.registry import tool
from mira.runtime.store import connect
from mira.runtime.timewords import describe, parse_when


class CreateReminderArgs(BaseModel):
    text: str = Field(..., description="What to be reminded about.")
    when: str | None = Field(
        default=None,
        description=(
            "Free-form time hint. Supported: 'in 20 min', 'in 2 hours', "
            "'at 3pm', 'tomorrow at 9am', 'monday 10am', ISO like "
            "'2026-04-20 14:00'. Unparseable hints are stored verbatim and "
            "the reminder won't auto-fire."
        ),
    )


class ListRemindersArgs(BaseModel):
    status: str = Field(default="open", description="'open' | 'done' | 'all'")
    limit: int = Field(default=10, ge=1, le=50)


class CompleteReminderArgs(BaseModel):
    id: int = Field(..., ge=1)


class DeleteReminderArgs(BaseModel):
    id: int = Field(..., ge=1)


@tool(
    "reminder.create",
    description=(
        "Create a local reminder. Returns id + parsed fire-at + human "
        "description. If `when` is unparseable, fire_at is null and the "
        "reminder is stored as a standalone todo."
    ),
    params=CreateReminderArgs,
    tags=("reminders",),
)
async def reminder_create(args: CreateReminderArgs) -> dict[str, Any]:
    now = time.time()
    hint = (args.when or "").strip() or None
    fire_at = parse_when(hint, now=now) if hint else None
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO reminders (text, when_hint, fire_at, status, created_at) "
            "VALUES (?, ?, ?, 'open', ?)",
            (args.text.strip(), hint, fire_at, now),
        )
        rid = cur.lastrowid
    return {
        "id": rid,
        "text": args.text.strip(),
        "when": hint,
        "fire_at": fire_at,
        "fire_at_human": describe(fire_at, now=now) if fire_at is not None else None,
        "status": "open",
    }


def _summarize_reminders(data: Any, *, max_items: int = 10) -> str:
    if not isinstance(data, dict):
        return str(data)
    items = data.get("items") or []
    count = data.get("count", len(items))
    if not items:
        return "0 reminders."
    parts: list[str] = [f"{count} reminder{'s' if count != 1 else ''}:"]
    for r in items[:max_items]:
        text = (r.get("text") or "").strip()
        when = r.get("fire_at_human") or r.get("when") or ""
        rid = r.get("id")
        line = f"[{rid}] {text}"
        if when:
            line += f" ({when})"
        parts.append(line)
    if len(items) > max_items:
        parts.append(f"...+{len(items) - max_items} more")
    return " | ".join(parts)


@tool(
    "reminder.list",
    description="List reminders. Default status='open'. Read-only.",
    params=ListRemindersArgs,
    tags=("reminders",),
    summarizer=_summarize_reminders,
)
async def reminder_list(args: ListRemindersArgs) -> dict[str, Any]:
    status = args.status.lower()
    query = (
        "SELECT id, text, when_hint, fire_at, status, created_at, "
        "completed_at FROM reminders"
    )
    params: list[Any] = []
    if status in ("open", "done"):
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY COALESCE(fire_at, created_at) ASC LIMIT ?"
    params.append(args.limit)

    with connect() as conn:
        rows = conn.execute(query, params).fetchall()

    now = time.time()
    items = [
        {
            "id": r["id"],
            "text": r["text"],
            "when": r["when_hint"],
            "fire_at": r["fire_at"],
            "fire_at_human": describe(r["fire_at"], now=now) if r["fire_at"] else None,
            "status": r["status"],
            "created_at": r["created_at"],
            "completed_at": r["completed_at"],
        }
        for r in rows
    ]
    return {"count": len(items), "items": items}


@tool(
    "reminder.complete",
    description="Mark a reminder done. Low-risk state change; no confirmation.",
    params=CompleteReminderArgs,
    tags=("reminders",),
)
async def reminder_complete(args: CompleteReminderArgs) -> dict[str, Any]:
    now = time.time()
    with connect() as conn:
        cur = conn.execute(
            "UPDATE reminders SET status='done', completed_at=? WHERE id=? AND status != 'done'",
            (now, args.id),
        )
        changed = cur.rowcount
    return {"id": args.id, "completed": bool(changed)}


@tool(
    "reminder.delete",
    description="Delete a reminder permanently. Destructive — requires confirmation.",
    params=DeleteReminderArgs,
    requires_confirmation=True,
    tags=("reminders",),
    success_phrase="Deleted.",
)
async def reminder_delete(args: DeleteReminderArgs) -> dict[str, Any]:
    with connect() as conn:
        cur = conn.execute("DELETE FROM reminders WHERE id = ?", (args.id,))
        deleted = cur.rowcount
    return {"id": args.id, "deleted": bool(deleted)}
