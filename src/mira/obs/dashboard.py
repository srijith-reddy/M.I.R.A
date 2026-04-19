from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from mira.config.settings import get_settings
from mira.obs.logging import log_event

# ---------- DB queries ----------


def _recent_turns(limit: int = 50) -> list[dict[str, Any]]:
    from mira.runtime.store import connect

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT turn_id, user_id, transcript, reply, status, via,
                   started_at, ended_at, latency_ms, cost_usd
            FROM turns
            ORDER BY COALESCE(ended_at, started_at) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def _llm_spend_24h() -> list[dict[str, Any]]:
    """Roll up `llm.call` events into per-model spend + call counts.

    Reads cost/model out of each event's `fields_json` via `json_extract`
    so we don't have to hydrate the row in Python. Sorted descending by
    cost so the most expensive model lands at the top of the dashboard —
    the whole point of this panel is to make "gpt-4o ate my budget"
    immediately visible.
    """
    from mira.runtime.store import connect

    cutoff = time.time() - 24 * 3600
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                json_extract(fields_json, '$.model')    AS model,
                json_extract(fields_json, '$.provider') AS provider,
                COUNT(*)                                AS calls,
                COALESCE(SUM(CAST(json_extract(fields_json, '$.cost_usd') AS REAL)), 0.0) AS cost_usd,
                COALESCE(SUM(CAST(json_extract(fields_json, '$.prompt_tokens') AS INTEGER)), 0) AS prompt_tokens,
                COALESCE(SUM(CAST(json_extract(fields_json, '$.completion_tokens') AS INTEGER)), 0) AS completion_tokens
            FROM events
            WHERE event = 'llm.call' AND ts >= ?
            GROUP BY model, provider
            ORDER BY cost_usd DESC
            """,
            (cutoff,),
        ).fetchall()
    return [
        {
            "model": r["model"] or "—",
            "provider": r["provider"] or "—",
            "calls": int(r["calls"]),
            "cost_usd": round(float(r["cost_usd"] or 0.0), 6),
            "prompt_tokens": int(r["prompt_tokens"] or 0),
            "completion_tokens": int(r["completion_tokens"] or 0),
        }
        for r in rows
    ]


def _stats_24h() -> dict[str, Any]:
    from mira.runtime.store import connect

    cutoff = time.time() - 24 * 3600
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT status, latency_ms, cost_usd FROM turns
            WHERE COALESCE(ended_at, started_at, 0) >= ?
            """,
            (cutoff,),
        ).fetchall()
    turns = len(rows)
    errors = sum(1 for r in rows if (r["status"] or "") == "error")
    cost = sum(float(r["cost_usd"] or 0.0) for r in rows)
    latencies = sorted(
        float(r["latency_ms"]) for r in rows if r["latency_ms"] is not None
    )
    return {
        "turns_24h": turns,
        "errors_24h": errors,
        "cost_usd_24h": round(cost, 6),
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
    }


def _events_for(turn_id: str, limit: int = 500) -> list[dict[str, Any]]:
    from mira.runtime.store import connect

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT ts, turn_id, span_id, parent_span_id, event, fields_json
            FROM events
            WHERE turn_id = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (turn_id, limit),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            fields = json.loads(r["fields_json"]) if r["fields_json"] else {}
        except Exception:
            fields = {"_raw": r["fields_json"]}
        out.append(
            {
                "ts": r["ts"],
                "turn_id": r["turn_id"],
                "span_id": r["span_id"],
                "parent_span_id": r["parent_span_id"],
                "event": r["event"],
                "fields": fields,
            }
        )
    return out


def _percentile(sorted_vals: list[float], pct: float) -> float | None:
    if not sorted_vals:
        return None
    # Nearest-rank method. Good enough for a 50/95 sketch; we don't need
    # linear interpolation for an ops dashboard.
    idx = max(0, min(len(sorted_vals) - 1, int(round(pct * (len(sorted_vals) - 1)))))
    return round(sorted_vals[idx], 2)


# ---------- HTTP ----------


class _Handler(BaseHTTPRequestHandler):
    # Suppress the default stderr access log — we have structured logging.
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401
        return

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self._send(status, body, "application/json")

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler contract)
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)

            if path == "/" or path == "/index.html":
                # JSON-only server — the Swift app renders the dashboard.
                self._send_json({
                    "service": "mira-dashboard",
                    "ui": "swift",
                    "endpoints": [
                        "/api/turns", "/api/stats",
                        "/api/llm_spend", "/api/events",
                    ],
                })
                return
            if path == "/health":
                self._send_json({"ok": True})
                return
            if path == "/api/turns":
                limit = _int_arg(qs, "limit", default=50, lo=1, hi=500)
                self._send_json({"items": _recent_turns(limit)})
                return
            if path == "/api/stats":
                self._send_json(_stats_24h())
                return
            if path == "/api/llm_spend":
                self._send_json({"items": _llm_spend_24h()})
                return
            if path == "/api/events":
                turn_id = (qs.get("turn_id") or [""])[0]
                if not turn_id:
                    self._send_json({"error": "turn_id required"}, status=400)
                    return
                limit = _int_arg(qs, "limit", default=500, lo=1, hi=5000)
                self._send_json({"items": _events_for(turn_id, limit)})
                return

            self._send(404, b"not found\n", "text/plain; charset=utf-8")
        except Exception as exc:
            # Don't leak a stack to the response; log it and return a
            # generic 500 so the dashboard stays usable.
            log_event("dashboard.handler_error", path=self.path, error=repr(exc))
            self._send_json({"error": "internal"}, status=500)


def _int_arg(qs: dict[str, list[str]], key: str, *, default: int, lo: int, hi: int) -> int:
    try:
        v = int((qs.get(key) or [str(default)])[0])
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


# ---------- lifecycle ----------


class DashboardServer:
    """Thin wrapper around `ThreadingHTTPServer` so callers don't have to
    manage the serving thread themselves. Bound to loopback only — a
    public port here would be a data-exfiltration surface (events contain
    transcripts and tool args)."""

    def __init__(self, host: str = "127.0.0.1", port: int | None = None) -> None:
        self.host = host
        self.port = port if port is not None else get_settings().dashboard_port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._httpd is not None:
            return
        try:
            self._httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
        except OSError as exc:
            # Port already in use (another MIRA, an old zombie, a dev server).
            # Log and continue — the daemon should not die over this.
            log_event(
                "dashboard.bind_failed", host=self.host, port=self.port, error=repr(exc)
            )
            self._httpd = None
            return
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="mira-dashboard",
            daemon=True,
        )
        self._thread.start()
        log_event("dashboard.started", host=self.host, port=self.port)

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception as exc:
                log_event("dashboard.stop_error", error=repr(exc))
        self._httpd = None
        self._thread = None

    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"


_server: DashboardServer | None = None


def dashboard() -> DashboardServer:
    global _server
    if _server is None:
        _server = DashboardServer()
    return _server


def serve_blocking(port: int | None = None) -> int:
    """CLI helper — start the server and block forever. Used by
    `mira dashboard` for dev (main process doesn't need the daemon)."""
    srv = DashboardServer(port=port)
    srv.start()
    if srv._httpd is None:
        return 1
    print(f"MIRA dashboard: {srv.url()}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.stop()
    return 0
