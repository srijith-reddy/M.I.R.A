from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from mira.config.settings import get_settings
from mira.obs.logging import add_event_listener, log_event

# Local WebSocket bridge for the SwiftUI HUD. Single source of truth for
# "what is MIRA doing right now" — every structured event published via
# `log_event` is mirrored to connected clients, and a small command set
# flows back in (user pressed stop, barge-in, submitted text).
#
# Transport:
#   * Loopback only (127.0.0.1). No auth, no TLS — same trust model as the
#     dashboard HTTP server: if you can hit the port, you're already on
#     the machine.
#   * JSON text frames. Each frame is a single `Frame` object (below).
#   * Protocol version pinned with `v=1`. Bump when adding fields the
#     client must understand; additive fields don't need a bump.
#
# Why log_event as the source (not the bus): log_event is the firehose for
# *everything* — wake triggers, agent steps, LLM calls with latency and
# cost, tool dispatches, reminders. The bus only carries a subset. Tapping
# log_event gives the UI one authoritative stream without us having to
# enumerate topics.

PROTOCOL_VERSION = 1

# Events the UI actually wants. Log_event fires for hundreds of different
# event names; forwarding all of them would drown the client and leak
# internals. This allowlist is curated — grow it deliberately.
_UI_EVENTS: frozenset[str] = frozenset({
    # Voice state + wake.
    "ui.state",
    "wake.triggered",
    "voice.transcript",
    "voice.followup_transcript",
    "voice.barge_in_followup",
    "voice.level",
    # Agent activity — what's MIRA doing right now.
    "supervisor.delegate",
    "supervisor.reply",
    "agent.dispatch",
    "browser_agent.confirmation_required",
    "commerce.confirmation_required",
    # Tool calls.
    "tool.dispatch",
    "tool.result",
    "web.search.denied",
    # LLM timing + cost — drives the subtle "thinking" surface.
    "llm.call",
    # Reminders + memory — surfaced as cards.
    "reminder.fired",
    "reminder.created",
    "memory.recalled",
    # Card payload: the HUD renders a structured panel next to the pill.
    # Emitted by the orchestrator when an agent sets ui_payload OR the
    # reply auto-parses into a list card. See mira.ui.cards.
    "ui.card",
    # Errors — surfaced as a subtle red line, not a modal.
    "voice.loop_error",
    "browser.error",
    "web.search.error",
    # HUD visibility nudges — the menubar orb forwards these when clicked.
    # The NSPanel itself stays always-visible; the HTML manages opacity.
    "ui.show_pill",
    "ui.hide_pill",
})


class UIBridge:
    """Async WebSocket server that mirrors log_event to connected clients.

    Lifecycle:
      * `start()` is called on the menubar's asyncio loop. It creates the
        server and registers a log_event listener.
      * The listener is called synchronously from any thread; we hop onto
        the event loop via `call_soon_threadsafe` before touching the
        clients set.
      * `stop()` cancels the server and unsubscribes.
    """

    def __init__(self, *, host: str = "127.0.0.1", port: int = 17651) -> None:
        self.host = host
        self.port = port
        self._server: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._clients: set[Any] = set()
        self._unsub: Any = None
        # Command callback — set by the voice loop (or whatever owns
        # pipeline lifecycle) after it starts. Bridge stays decoupled from
        # VoiceLoop; whoever wants to handle HUD commands registers here.
        # Signature: `async fn(cmd_type: str, data: dict) -> None` or sync.
        self._command_handler: Any = None

    def set_command_handler(self, fn: Any) -> None:
        self._command_handler = fn

    async def start(self) -> None:
        # Lazy import so the core daemon doesn't pay for the `websockets`
        # dep unless the bridge is actually enabled.
        try:
            import websockets
        except ImportError:
            log_event(
                "ui_bridge.import_failed",
                hint="pip install websockets",
            )
            return

        self._loop = asyncio.get_running_loop()
        try:
            self._server = await websockets.serve(
                self._handle_client,
                self.host,
                self.port,
                ping_interval=20,
                ping_timeout=20,
                max_size=2**20,  # 1 MiB cap — UI frames are tiny; guard against abuse.
            )
        except OSError as exc:
            # Port conflict (another MIRA instance, stale zombie). Log and
            # bail — the daemon should not die because the UI bridge lost
            # a coin flip on port binding.
            log_event(
                "ui_bridge.bind_failed",
                host=self.host,
                port=self.port,
                error=repr(exc),
            )
            return

        self._unsub = add_event_listener(self._on_event)
        log_event("ui_bridge.started", host=self.host, port=self.port)

    async def stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None
        # Close any surviving client connections so the OS releases the
        # sockets immediately rather than waiting on keepalive.
        for ws in list(self._clients):
            try:
                await ws.close()
            except Exception:
                pass
        self._clients.clear()
        log_event("ui_bridge.stopped")

    # ---------- Client handling ----------

    async def _handle_client(self, ws: Any) -> None:
        self._clients.add(ws)
        try:
            await ws.send(
                _frame(
                    "hello",
                    {"protocol": PROTOCOL_VERSION, "app": "mira"},
                )
            )
            async for raw in ws:
                await self._on_command(raw)
        except Exception:
            # Client disconnected or sent garbage. Not our problem — drop it.
            pass
        finally:
            self._clients.discard(ws)

    async def _on_command(self, raw: Any) -> None:
        try:
            msg = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode("utf-8"))
        except Exception:
            return
        ctype = msg.get("type")
        data = msg.get("data") or {}

        # Command surface is intentionally small. Adding a command means
        # thinking about the failure mode if two clients send it at once.
        if ctype == "cmd.stop":
            log_event("ui_bridge.cmd_stop")
        elif ctype == "cmd.barge_in":
            log_event("ui_bridge.cmd_barge_in")
        elif ctype == "cmd.submit_text":
            text = str(data.get("text") or "").strip()
            if not text:
                return
            log_event("ui_bridge.cmd_submit_text", text=text[:200])
            data = {"text": text}
        elif ctype == "cmd.quit":
            # SIGTERM routes into MiraDaemon's signal handler, which runs
            # the full async shutdown (Playwright / httpx / crawl4ai).
            # os._exit would leave those processes as zombies.
            log_event("ui_bridge.cmd_quit")
            try:
                import os
                import signal
                os.kill(os.getpid(), signal.SIGTERM)
            except Exception as exc:
                log_event("ui_bridge.cmd_quit_error", error=repr(exc))
            return
        else:
            log_event("ui_bridge.cmd_unknown", type=ctype)
            return

        handler = self._command_handler
        if handler is None:
            return
        try:
            result = handler(ctype, data)
            if asyncio.iscoroutine(result):
                # Schedule rather than await — we don't want a misbehaving
                # handler to block the client's command stream. Errors are
                # surfaced via the task's exception callback.
                task = asyncio.create_task(result)
                task.add_done_callback(_log_task_exception)
        except Exception as exc:
            log_event("ui_bridge.handler_error", type=ctype, error=repr(exc))

    # ---------- Fan-out ----------

    def _on_event(self, event: str, fields: dict[str, Any]) -> None:
        """log_event callback. Invoked from arbitrary threads — must hop
        onto our asyncio loop before touching the clients set.

        Bus-published events land here as `bus.<topic>` (see bus.publish).
        The UI protocol uses the unprefixed topic, so we normalize before
        matching/forwarding — otherwise `ui.state` wouldn't match and the
        HUD orb would never update."""
        if event.startswith("bus."):
            event = event[4:]
        if event not in _UI_EVENTS:
            return
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._schedule_broadcast, event, fields)
        except RuntimeError:
            # Loop closed. Silent drop.
            pass

    def _schedule_broadcast(self, event: str, fields: dict[str, Any]) -> None:
        if not self._clients:
            return
        asyncio.create_task(self._broadcast(event, fields))

    async def _broadcast(self, event: str, fields: dict[str, Any]) -> None:
        if not self._clients:
            return
        text = _frame(event, fields)
        dead: list[Any] = []
        for ws in self._clients:
            try:
                await ws.send(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)


def _log_task_exception(task: asyncio.Task) -> None:
    try:
        exc = task.exception()
    except (asyncio.CancelledError, asyncio.InvalidStateError):
        return
    if exc is not None:
        log_event("ui_bridge.handler_task_error", error=repr(exc))


def _frame(event: str, data: dict[str, Any]) -> str:
    payload = {
        "v": PROTOCOL_VERSION,
        "type": event,
        "ts": time.time(),
        "data": _jsonable(data),
    }
    return json.dumps(payload, default=str)


def _jsonable(obj: Any) -> Any:
    # Defensive: field dicts occasionally contain objects that json.dumps
    # chokes on (pydantic, datetime, numpy scalars). The default=str
    # fallback on dumps covers most; we still normalize dict values here so
    # nested structures don't trip the serializer.
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


# ---------- Singleton accessor ----------

_bridge: UIBridge | None = None


def ui_bridge() -> UIBridge:
    global _bridge
    if _bridge is None:
        s = get_settings()
        _bridge = UIBridge(port=s.ui_bridge_port)
    return _bridge
