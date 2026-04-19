from __future__ import annotations

import json
import time
from typing import Any

# NOTE: this module is imported from obs.logging — which is the lowest layer
# in the stack. Imports here MUST stay lightweight and must NOT pull in
# settings, store, or anything that might call `log_event` during init (that
# would re-enter this module and deadlock the recording path). All heavier
# imports are deferred into function bodies.


def record_event(
    event: str,
    fields: dict[str, Any],
    *,
    turn_id: str | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
) -> None:
    """Best-effort event → SQLite insert.

    Called from the hot path after every `log_event`. Any failure here must
    NOT propagate — observability should never break the voice pipeline.
    Errors land in stderr via the stdlib logger so we don't recursively
    call `log_event`.
    """
    if not _persist_enabled():
        return
    try:
        from mira.runtime.store import connect

        payload = json.dumps(
            {k: _safe(v) for k, v in fields.items()},
            default=str,
        )
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO events (ts, turn_id, span_id, parent_span_id, event, fields_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    time.time(),
                    turn_id,
                    span_id,
                    parent_span_id,
                    event,
                    payload,
                ),
            )
    except Exception as exc:  # pragma: no cover - defensive
        _swallow("record_event", exc)


def record_turn(
    *,
    turn_id: str,
    user_id: str,
    transcript: str,
    reply: str,
    status: str,
    via: str,
    started_at: float | None = None,
    ended_at: float | None = None,
    latency_ms: float | None = None,
) -> None:
    """Upsert a row in `turns`. Cost is derived by scanning LLM events
    attached to this turn_id — so the caller doesn't need to accumulate
    cost along the way (which would require plumbing a contextvar through
    every agent). The events table is the single source of truth for
    tokens/cost; `turns.cost_usd` is a denormalized roll-up for fast
    listing."""
    try:
        from mira.runtime.store import connect

        now = time.time()
        end_ts = ended_at if ended_at is not None else now
        cost_usd = _sum_turn_cost(turn_id)
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO turns (
                    turn_id, user_id, transcript, reply, status, via,
                    started_at, ended_at, latency_ms, cost_usd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(turn_id) DO UPDATE SET
                    transcript = excluded.transcript,
                    reply = excluded.reply,
                    status = excluded.status,
                    via = excluded.via,
                    ended_at = excluded.ended_at,
                    latency_ms = excluded.latency_ms,
                    cost_usd = excluded.cost_usd
                """,
                (
                    turn_id,
                    user_id,
                    transcript,
                    reply,
                    status,
                    via,
                    started_at,
                    end_ts,
                    latency_ms,
                    cost_usd,
                ),
            )
    except Exception as exc:  # pragma: no cover - defensive
        _swallow("record_turn", exc)


def _sum_turn_cost(turn_id: str) -> float:
    """Scan `events` for this turn and sum `llm.call` cost_usd fields.
    Returns 0.0 on any error (cost is nice-to-have, not load-bearing)."""
    try:
        from mira.runtime.store import connect

        total = 0.0
        with connect() as conn:
            rows = conn.execute(
                "SELECT fields_json FROM events WHERE turn_id = ? AND event = 'llm.call'",
                (turn_id,),
            ).fetchall()
        for r in rows:
            try:
                data = json.loads(r["fields_json"] or "{}")
            except Exception:
                continue
            try:
                total += float(data.get("cost_usd") or 0.0)
            except (TypeError, ValueError):
                continue
        return total
    except Exception:
        return 0.0


# ---------- helpers ----------


def _persist_enabled() -> bool:
    """Cached per call — settings lookup is cheap but we still want a single
    boolean check on the hot path. Guarded because some test paths import
    this module before settings are ready."""
    try:
        from mira.config.settings import get_settings

        return bool(getattr(get_settings(), "events_persist", True))
    except Exception:
        return True


def _safe(value: Any) -> Any:
    """JSON-serializable projection for event fields. Anything exotic
    (datetime, Path, dataclass) falls through to default=str on dump."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_safe(x) for x in value]
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    return str(value)


def _swallow(where: str, exc: BaseException) -> None:
    """Log-to-stderr fallback for recorder failures. We deliberately don't
    call `log_event` here — that's what's calling us, and a loop is worse
    than a silent gap in the events table."""
    import logging

    logging.getLogger("mira.recorder").warning("%s failed: %r", where, exc)
