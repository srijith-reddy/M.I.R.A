"""Shared helper for prepending recent-turn history to a specialist's
message list.

Without this, each specialist's `_run` starts a fresh chat window with
only the current transcript. That breaks multi-turn flows — e.g. commerce
asks "which size?", the user says "14 inch", and commerce has no memory
of the MacBook context it just asked about.

The orchestrator stuffs the last few completed turns into
`AgentRequest.context["recent_turns"]` as a list of `{transcript, reply}`
dicts. Specialists call `prepend_history()` right before appending the
current user message so the planner sees the full arc.

Kept defensive — a malformed list, missing fields, or empty context is a
no-op so a bad state in session never breaks a turn.
"""

from __future__ import annotations

from typing import Any

from mira.runtime.llm import Message


def prepend_history(
    messages: list[Message], context: dict[str, Any] | None
) -> None:
    """Insert recent-turn history into `messages` just after the system
    prompt and before the current user message. Mutates in place."""
    if not isinstance(context, dict):
        return
    recent = context.get("recent_turns")
    if not isinstance(recent, list) or not recent:
        return
    history: list[Message] = []
    for t in recent:
        if not isinstance(t, dict):
            continue
        q = (t.get("transcript") or "").strip()
        a = (t.get("reply") or "").strip()
        if q:
            history.append(Message(role="user", content=q))
        if a:
            history.append(Message(role="assistant", content=a))
    if not history:
        return
    # Insert after the system message (index 0). If there's no system
    # message, history goes at the front.
    insert_at = 1 if messages and messages[0].role == "system" else 0
    messages[insert_at:insert_at] = history
