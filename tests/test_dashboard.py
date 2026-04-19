from __future__ import annotations

import http.client
import json
import time

import pytest

from mira.obs.dashboard import (
    DashboardServer,
    _events_for,
    _percentile,
    _recent_turns,
    _stats_24h,
)
from mira.obs.recorder import record_event, record_turn
from mira.runtime.store import connect


def _now() -> float:
    return time.time()


# ---------- recorder → DB ----------


def test_record_event_persists_row() -> None:
    record_event(
        "test.event",
        {"foo": "bar", "n": 3},
        turn_id="t-abc",
        span_id="s-1",
        parent_span_id=None,
    )
    with connect() as conn:
        rows = conn.execute(
            "SELECT turn_id, span_id, event, fields_json FROM events WHERE turn_id = ?",
            ("t-abc",),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["event"] == "test.event"
    assert rows[0]["span_id"] == "s-1"
    payload = json.loads(rows[0]["fields_json"])
    assert payload == {"foo": "bar", "n": 3}


def test_record_turn_aggregates_cost_from_prior_llm_events() -> None:
    # Simulate two LLM calls during turn t-sum.
    record_event(
        "llm.call",
        {"model": "gpt-4o", "cost_usd": 0.01, "prompt_tokens": 1000, "completion_tokens": 200},
        turn_id="t-sum",
    )
    record_event(
        "llm.call",
        {"model": "gpt-4o", "cost_usd": 0.02, "prompt_tokens": 2000, "completion_tokens": 400},
        turn_id="t-sum",
    )
    # Non-llm event must not contribute.
    record_event("tool.dispatch", {"cost_usd": 99.0}, turn_id="t-sum")

    record_turn(
        turn_id="t-sum",
        user_id="local",
        transcript="hello",
        reply="hi",
        status="done",
        via="direct:research",
        started_at=_now() - 1.0,
        ended_at=_now(),
        latency_ms=1000,
    )

    with connect() as conn:
        row = conn.execute(
            "SELECT cost_usd, status FROM turns WHERE turn_id = ?", ("t-sum",)
        ).fetchone()
    assert row is not None
    assert row["status"] == "done"
    assert row["cost_usd"] == pytest.approx(0.03, abs=1e-9)


def test_record_turn_upserts_on_second_call() -> None:
    record_turn(
        turn_id="t-upsert",
        user_id="local",
        transcript="first",
        reply="a",
        status="done",
        via="smalltalk",
        latency_ms=100,
    )
    record_turn(
        turn_id="t-upsert",
        user_id="local",
        transcript="first",
        reply="updated reply",
        status="error",
        via="smalltalk",
        latency_ms=250,
    )
    with connect() as conn:
        rows = conn.execute(
            "SELECT reply, status, latency_ms FROM turns WHERE turn_id = ?",
            ("t-upsert",),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["reply"] == "updated reply"
    assert rows[0]["status"] == "error"
    assert rows[0]["latency_ms"] == 250


# ---------- query helpers ----------


def test_recent_turns_orders_by_ended_at_desc() -> None:
    now = _now()
    for i, ts in enumerate([now - 30, now - 10, now - 20]):
        record_turn(
            turn_id=f"t-order-{i}",
            user_id="local",
            transcript=f"q{i}",
            reply=f"r{i}",
            status="done",
            via="smalltalk",
            started_at=ts - 0.5,
            ended_at=ts,
            latency_ms=100,
        )
    items = _recent_turns(limit=50)
    mine = [r for r in items if r["turn_id"].startswith("t-order-")]
    # Newest (i=1, ts=now-10) first, then i=2 (now-20), then i=0 (now-30).
    assert [r["turn_id"] for r in mine[:3]] == ["t-order-1", "t-order-2", "t-order-0"]


def test_stats_24h_counts_errors_and_sums_cost() -> None:
    # Inject an llm.call event then a turn so cost flows through.
    record_event(
        "llm.call",
        {"cost_usd": 0.005, "prompt_tokens": 100, "completion_tokens": 50},
        turn_id="t-stat-1",
    )
    record_turn(
        turn_id="t-stat-1",
        user_id="local",
        transcript="ok",
        reply="ok",
        status="done",
        via="smalltalk",
        started_at=_now() - 1,
        ended_at=_now(),
        latency_ms=100,
    )
    record_turn(
        turn_id="t-stat-2",
        user_id="local",
        transcript="bad",
        reply="err",
        status="error",
        via="smalltalk",
        started_at=_now() - 1,
        ended_at=_now(),
        latency_ms=400,
    )
    s = _stats_24h()
    assert s["turns_24h"] >= 2
    assert s["errors_24h"] >= 1
    assert s["cost_usd_24h"] >= 0.005 - 1e-9


def test_events_for_returns_turn_trace_in_order() -> None:
    for name in ["span.start", "llm.call", "span.end"]:
        record_event(name, {"k": name}, turn_id="t-trace")
    items = _events_for("t-trace")
    assert [e["event"] for e in items] == ["span.start", "llm.call", "span.end"]


def test_percentile_handles_empty_and_populated() -> None:
    assert _percentile([], 0.5) is None
    assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5) == 3.0
    assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.95) == 5.0


# ---------- HTTP endpoints ----------


@pytest.fixture
def live_server() -> DashboardServer:
    srv = DashboardServer(host="127.0.0.1", port=0)
    # port=0 → stdlib picks a free port. We can't use that shortcut through
    # ThreadingHTTPServer's __init__ directly because we want to observe the
    # chosen port; easiest workaround is to pick one.
    srv.port = _pick_free_port()
    srv.start()
    assert srv._httpd is not None, "server failed to bind"
    yield srv
    srv.stop()


def _pick_free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http_json(srv: DashboardServer, path: str) -> tuple[int, dict]:
    conn = http.client.HTTPConnection(srv.host, srv.port, timeout=5.0)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read().decode("utf-8")
    conn.close()
    try:
        return resp.status, json.loads(body)
    except json.JSONDecodeError:
        return resp.status, {"_raw": body}


def test_health_endpoint_responds_ok(live_server: DashboardServer) -> None:
    status, body = _http_json(live_server, "/health")
    assert status == 200
    assert body == {"ok": True}


def test_index_returns_json_manifest(live_server: DashboardServer) -> None:
    # HTML dashboard was removed — the root now returns a JSON manifest
    # pointing at the /api/* endpoints the Swift app consumes.
    status, body = _http_json(live_server, "/")
    assert status == 200
    assert body.get("service") == "mira-dashboard"
    assert "/api/turns" in body.get("endpoints", [])


def test_turns_endpoint_returns_items_array(live_server: DashboardServer) -> None:
    record_turn(
        turn_id="t-http-1",
        user_id="local",
        transcript="hello",
        reply="hi",
        status="done",
        via="smalltalk",
        started_at=_now() - 1,
        ended_at=_now(),
        latency_ms=42,
    )
    status, body = _http_json(live_server, "/api/turns?limit=10")
    assert status == 200
    assert "items" in body
    ids = {r["turn_id"] for r in body["items"]}
    assert "t-http-1" in ids


def test_events_endpoint_requires_turn_id(live_server: DashboardServer) -> None:
    status, body = _http_json(live_server, "/api/events")
    assert status == 400
    assert "error" in body


def test_events_endpoint_returns_trace(live_server: DashboardServer) -> None:
    record_event("span.start", {"name": "root"}, turn_id="t-http-2")
    record_event("span.end", {"name": "root", "latency_ms": 5}, turn_id="t-http-2")
    status, body = _http_json(live_server, "/api/events?turn_id=t-http-2")
    assert status == 200
    events = [e["event"] for e in body["items"]]
    assert events == ["span.start", "span.end"]


def test_unknown_path_returns_404(live_server: DashboardServer) -> None:
    conn = http.client.HTTPConnection(live_server.host, live_server.port, timeout=5.0)
    conn.request("GET", "/nope")
    resp = conn.getresponse()
    resp.read()
    conn.close()
    assert resp.status == 404
