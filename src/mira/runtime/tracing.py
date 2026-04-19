from __future__ import annotations

import contextvars
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

_current_turn: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mira_turn_id", default=None
)
_current_span: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mira_span_id", default=None
)


def current_turn_id() -> str | None:
    return _current_turn.get()


def current_span_id() -> str | None:
    return _current_span.get()


def _short_id() -> str:
    return uuid.uuid4().hex[:12]


def _emit(event: str, **fields: Any) -> None:
    # Lazy import to avoid circular dependency at package init.
    from mira.obs.logging import log_event

    log_event(event, **fields)


@contextmanager
def turn_context(turn_id: str | None = None) -> Iterator[str]:
    tid = turn_id or _short_id()
    token = _current_turn.set(tid)
    _emit("turn.start", turn_id=tid)
    t0 = time.perf_counter()
    status = "ok"
    err: str | None = None
    try:
        yield tid
    except BaseException as exc:
        status = "error"
        err = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        _emit(
            "turn.end",
            turn_id=tid,
            status=status,
            error=err,
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        )
        _current_turn.reset(token)


@contextmanager
def span(name: str, **fields: Any) -> Iterator[str]:
    sid = _short_id()
    parent = _current_span.get()
    token = _current_span.set(sid)
    t0 = time.perf_counter()
    _emit("span.start", span_id=sid, parent_id=parent, name=name, **fields)
    status = "ok"
    err: str | None = None
    try:
        yield sid
    except BaseException as exc:
        status = "error"
        err = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        _emit(
            "span.end",
            span_id=sid,
            parent_id=parent,
            name=name,
            status=status,
            error=err,
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            **fields,
        )
        _current_span.reset(token)
