from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from mira.config.paths import paths
from mira.config.settings import get_settings

_RESERVED = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "module", "msecs",
    "message", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_configured = False

# Fan-out hook for the UI bridge. Listeners are called synchronously inside
# `log_event`, so they must be cheap and never raise — the bridge itself
# wraps its work in a try/except and queues onto its own asyncio loop.
_event_listeners: list[Any] = []


def add_event_listener(fn: Any) -> Any:
    """Register a `fn(event: str, fields: dict)` callback invoked for every
    log_event. Returns an unsubscribe function. The callback must be cheap
    and non-raising — any work that can block belongs behind an asyncio
    queue on the caller side."""
    _event_listeners.append(fn)

    def _unsub() -> None:
        try:
            _event_listeners.remove(fn)
        except ValueError:
            pass

    return _unsub


def setup_logging() -> None:
    global _configured
    if _configured:
        return

    settings = get_settings()
    paths.ensure()

    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)

    file_handler = logging.FileHandler(paths.logs_dir / "mira.log")
    file_handler.setFormatter(JsonFormatter())
    root.addHandler(file_handler)

    console = logging.StreamHandler(sys.stderr)
    if settings.log_json:
        console.setFormatter(JsonFormatter())
    else:
        console.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
    root.addHandler(console)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(event: str, **fields: Any) -> None:
    """Emit a structured event. Fields land as top-level JSON keys in the file log.

    Auto-enriches each event with the current trace context (turn_id, span_id)
    when one is active. The import is local to avoid an import cycle at package init.
    """
    # Local import: runtime.tracing depends on obs.logging for emission, so we
    # can't pull it in at module load time.
    try:
        from mira.runtime.tracing import current_span_id, current_turn_id

        tid = current_turn_id()
        sid = current_span_id()
    except Exception:
        tid = None
        sid = None

    # Rename any field that collides with a LogRecord built-in. Python's
    # `logger.info(..., extra=...)` raises KeyError if extra contains a
    # reserved name (name, msg, module, etc.). Callers don't know or care
    # which names are reserved — and this has bit us from inside wakeword
    # detection where `model.predict()` logs structured fields that
    # happened to include `name`. Prefix with `f_` instead of dropping so
    # the info isn't lost.
    safe_fields: dict[str, Any] = {}
    for k, v in fields.items():
        safe_fields[f"f_{k}" if k in _RESERVED else k] = v

    extra: dict[str, Any] = {"event": event, **safe_fields}
    if tid is not None and "turn_id" not in extra:
        extra["turn_id"] = tid
    if sid is not None and "ctx_span_id" not in extra:
        extra["ctx_span_id"] = sid

    logger = logging.getLogger("mira.event")
    logger.info(event, extra=extra)

    # Mirror to SQLite for the dashboard. Local import avoids a cycle at
    # module load (recorder eventually reaches back into obs.logging for
    # fallback logging on failure).
    try:
        from mira.obs.recorder import record_event

        record_event(event, fields, turn_id=tid, span_id=sid)
    except Exception:
        # Never let observability crash the caller. `record_event` already
        # swallows its own errors; this guard catches import-time issues.
        pass

    # Fan out to UI listeners (websocket bridge, etc). Each listener is
    # responsible for its own error handling; we still wrap here so a
    # misbehaving one can't take down log_event callers.
    if _event_listeners:
        payload = {**fields}
        if tid is not None and "turn_id" not in payload:
            payload["turn_id"] = tid
        for fn in list(_event_listeners):
            try:
                fn(event, payload)
            except Exception:
                pass
