from __future__ import annotations

import json
import time
from typing import Any

from pydantic import BaseModel, Field

from mira.obs.logging import log_event
from mira.runtime.schemas import ToolCall
from mira.runtime.store import connect

PENDING_TTL_SECONDS = 120


class PendingConfirmation(BaseModel):
    """One side-effectful tool call waiting on a user yes/no.

    Stored per-user. Only one pending at a time — if a new confirmation is
    raised before the last one resolves, the new one wins and the old is
    dropped. In practice users don't interleave these; cleaner than a queue
    we'd then have to explain to the user.
    """

    original_turn_id: str
    agent: str
    tool_call: ToolCall
    prompt: str
    created_at: float = Field(default_factory=lambda: time.time())

    def is_expired(self, now: float | None = None) -> bool:
        n = now if now is not None else time.time()
        return (n - self.created_at) > PENDING_TTL_SECONDS


class TurnRecord(BaseModel):
    turn_id: str
    transcript: str
    reply: str
    status: str
    via: str
    at: float = Field(default_factory=lambda: time.time())


_MAX_RECENT_TURNS = 20


def load_pending(user_id: str = "local") -> PendingConfirmation | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT pending_json, pending_created_at FROM session_state WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if row is None or not row["pending_json"]:
        return None
    try:
        obj = PendingConfirmation.model_validate_json(row["pending_json"])
    except Exception as exc:
        log_event("session.pending_parse_error", error=repr(exc))
        return None
    if obj.is_expired():
        clear_pending(user_id)
        return None
    return obj


def set_pending(p: PendingConfirmation, user_id: str = "local") -> None:
    now = time.time()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO session_state (user_id, pending_json, pending_created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                pending_json = excluded.pending_json,
                pending_created_at = excluded.pending_created_at,
                updated_at = excluded.updated_at
            """,
            (user_id, p.model_dump_json(), p.created_at, now),
        )
    log_event("session.pending_set", user_id=user_id, tool=p.tool_call.tool)


def clear_pending(user_id: str = "local") -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO session_state (user_id, pending_json, pending_created_at, updated_at)
            VALUES (?, NULL, NULL, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                pending_json = NULL,
                pending_created_at = NULL,
                updated_at = excluded.updated_at
            """,
            (user_id, time.time()),
        )
    log_event("session.pending_cleared", user_id=user_id)


def record_turn(
    *,
    turn_id: str,
    transcript: str,
    reply: str,
    status: str,
    via: str,
    user_id: str = "local",
) -> None:
    record = TurnRecord(
        turn_id=turn_id,
        transcript=transcript,
        reply=reply,
        status=status,
        via=via,
    )
    with connect() as conn:
        row = conn.execute(
            "SELECT recent_turns_json FROM session_state WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        existing: list[dict[str, Any]] = []
        if row and row["recent_turns_json"]:
            try:
                existing = json.loads(row["recent_turns_json"])
            except Exception:
                existing = []
        existing.append(record.model_dump())
        existing = existing[-_MAX_RECENT_TURNS:]
        conn.execute(
            """
            INSERT INTO session_state (user_id, recent_turns_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                recent_turns_json = excluded.recent_turns_json,
                updated_at = excluded.updated_at
            """,
            (user_id, json.dumps(existing), time.time()),
        )

    # Dashboard-facing summary row. Lives alongside the rolling-window JSON
    # above — the JSON drives recent-context for the agents, this row drives
    # the observability surface. Cost is derived from llm.call events
    # recorded during the turn (see obs.recorder._sum_turn_cost).
    try:
        from mira.obs.recorder import record_turn as _dash_record_turn

        _dash_record_turn(
            turn_id=turn_id,
            user_id=user_id,
            transcript=transcript,
            reply=reply,
            status=status,
            via=via,
        )
    except Exception:
        # Dashboard persistence is best-effort. Never break the voice path.
        pass


def recent_turns(user_id: str = "local", limit: int = 6) -> list[TurnRecord]:
    with connect() as conn:
        row = conn.execute(
            "SELECT recent_turns_json FROM session_state WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row or not row["recent_turns_json"]:
        return []
    try:
        raw = json.loads(row["recent_turns_json"])
    except Exception:
        return []
    out: list[TurnRecord] = []
    for r in raw[-limit:]:
        try:
            out.append(TurnRecord.model_validate(r))
        except Exception:
            continue
    return out


# ---- Confirmation-intent classifier ----------------------------------------

_YES = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "k", "kk",
    "please", "do it", "go ahead", "confirm", "confirmed", "absolutely",
    "of course", "fine", "go for it", "proceed",
}
_NO = {
    "no", "nope", "nah", "cancel", "stop", "don't", "do not",
    "nevermind", "never mind", "wait", "hold on", "abort", "scratch that",
}


def classify_confirmation(transcript: str) -> str:
    """Return 'yes' | 'no' | 'unclear'.

    Kept strictly deterministic — LLM classification here would be safer but
    adds 200–400ms to every turn where a confirmation is pending, which hits
    the exact latency budget we're most protective of. If this heuristic
    misfires in the wild we'll add an LLM fallback for the 'unclear' bucket.
    """
    t = transcript.strip().lower().rstrip(".!?")
    if not t:
        return "unclear"
    if t in _YES:
        return "yes"
    if t in _NO:
        return "no"
    for phrase in _YES:
        if t.startswith(phrase + " ") or t.startswith(phrase + ","):
            return "yes"
    for phrase in _NO:
        if t.startswith(phrase + " ") or t.startswith(phrase + ","):
            return "no"
    return "unclear"
