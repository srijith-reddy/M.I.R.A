from __future__ import annotations

import asyncio
import subprocess
from typing import Any

from pydantic import BaseModel, Field

from mira.runtime.registry import tool


def _osascript(script: str) -> tuple[bool, str]:
    """Run an AppleScript. Returns (ok, stdout_or_error)."""
    try:
        res = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        return False, "osascript timed out"
    if res.returncode != 0:
        return False, (res.stderr or "osascript failed").strip()
    return True, (res.stdout or "").strip()


class SendMessageArgs(BaseModel):
    recipient: str = Field(
        ..., description="Phone number (+15551234567), Apple ID email, or handle."
    )
    body: str = Field(..., description="Message text.")
    service: str = Field(
        default="iMessage",
        description="'iMessage' or 'SMS'. iMessage preferred for Apple handles.",
    )


@tool(
    "messages.send",
    description=(
        "Send an iMessage/SMS via macOS Messages.app. Destructive: requires "
        "confirmation. Recipient must be a phone number, email, or handle — "
        "use `contacts.lookup` first to resolve a name."
    ),
    params=SendMessageArgs,
    requires_confirmation=True,
    tags=("messaging",),
)
async def messages_send(args: SendMessageArgs) -> dict[str, Any]:
    body = args.body.replace("\\", "\\\\").replace('"', '\\"')
    recipient = args.recipient.replace('"', '\\"')
    service = "iMessage" if args.service.lower() == "imessage" else "SMS"
    script = f'''
    tell application "Messages"
        set targetService to 1st service whose service type = {service}
        set targetBuddy to buddy "{recipient}" of targetService
        send "{body}" to targetBuddy
    end tell
    '''
    ok, out = await asyncio.to_thread(_osascript, script)
    return {"ok": ok, "recipient": args.recipient, "service": service, "detail": out}


class ReadMessagesArgs(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)


def _summarize_recent(data: Any, *, max_items: int = 8) -> str:
    if not isinstance(data, dict):
        return str(data)
    if not data.get("ok"):
        return f"messages error: {data.get('error') or 'unknown'}"
    items = data.get("items") or []
    count = data.get("count", len(items))
    if not items:
        return "0 messages."
    parts: list[str] = [f"{count} message{'s' if count != 1 else ''}:"]
    for m in items[:max_items]:
        who = "me" if m.get("is_from_me") else (m.get("handle") or "unknown")
        text = (m.get("text") or "").strip().replace("\n", " ")
        if len(text) > 120:
            text = text[:120] + "..."
        ts = m.get("ts") or ""
        parts.append(f"{ts} {who}: {text}")
    if len(items) > max_items:
        parts.append(f"...+{len(items) - max_items} more")
    return " | ".join(parts)


@tool(
    "messages.recent",
    description=(
        "Read the N most recent iMessage/SMS threads (sender + snippet). "
        "Reads from the local chat.db; read-only."
    ),
    params=ReadMessagesArgs,
    tags=("messaging",),
    summarizer=_summarize_recent,
)
async def messages_recent(args: ReadMessagesArgs) -> dict[str, Any]:
    import os
    import sqlite3

    path = os.path.expanduser("~/Library/Messages/chat.db")
    if not os.path.exists(path):
        return {"ok": False, "error": "chat.db not accessible — grant Full Disk Access to MIRA"}

    def _read() -> list[dict[str, Any]]:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT h.id AS handle, m.text,
                       datetime(m.date/1000000000 + strftime('%s','2001-01-01'),
                                'unixepoch','localtime') AS ts,
                       m.is_from_me
                FROM message m
                LEFT JOIN handle h ON m.handle_id = h.ROWID
                WHERE m.text IS NOT NULL AND length(m.text) > 0
                ORDER BY m.date DESC
                LIMIT ?
                """,
                (args.limit,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    try:
        items = await asyncio.to_thread(_read)
        return {"ok": True, "count": len(items), "items": items}
    except Exception as exc:
        return {"ok": False, "error": repr(exc)}
