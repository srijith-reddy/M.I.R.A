from __future__ import annotations

import asyncio
import signal
import threading
from typing import Any

from mira.browser.runtime import browser
from mira.config.settings import get_settings
from mira.obs.dashboard import dashboard
from mira.obs.logging import log_event
from mira.obs.ui_bridge import ui_bridge
from mira.runtime.bus import bus
from mira.runtime.scheduler import scheduler
from mira.voice.loop import VoiceLoop


class MiraDaemon:
    """Headless process host. Owns the asyncio loop, voice pipeline,
    scheduler, dashboard server, and the WebSocket bridge that the Swift
    menubar app connects to. No menubar icon, no orb painting — the Swift
    app is the only user-facing surface now.

    Threading layout:
      * Main thread blocks on a signal event; SIGINT/SIGTERM unblocks it
        and triggers graceful shutdown.
      * `mira-asyncio` thread runs the event loop — VoiceLoop, bus,
        orchestrator, ui_bridge.
      * Dashboard server runs in its own thread (stdlib ThreadingHTTPServer).
      * Porcupine runs in its own thread inside VoiceLoop.
    """

    def __init__(self) -> None:
        self._state: str = "idle"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._voice: VoiceLoop | None = None
        self._stop_event = threading.Event()

    def run(self) -> None:
        if not _acquire_instance_lock():
            log_event("ui.duplicate_instance_detected")
            return

        missing = get_settings().missing_required_keys()
        if missing:
            log_event("ui.setup_required", missing=missing)
            # No menu UI to display this in anymore. The Swift app shows a
            # setup banner from its own missing-key check; the CLI's
            # `mira setup` wizard is the fix path.
            return

        ready = threading.Event()

        def _run_loop() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            ready.set()
            try:
                loop.run_forever()
            finally:
                loop.close()

        self._loop_thread = threading.Thread(
            target=_run_loop, name="mira-asyncio", daemon=True
        )
        self._loop_thread.start()
        ready.wait(timeout=5.0)
        assert self._loop is not None

        async def _on_ui_state(_topic: str, payload: dict[str, Any]) -> None:
            state = payload.get("state")
            if isinstance(state, str):
                self._state = state

        bus().subscribe("ui.state", _on_ui_state)

        # Warm the agent + tool registry on the asyncio thread before the
        # first wake. Cold path imports ~40 tool modules and inits pydantic
        # schemas — ~300-600ms that the user would otherwise wait through on
        # turn 1. Idempotent, so run_turn's later call short-circuits.
        def _warm() -> None:
            from mira.agents import install_default_agents as _warm_agents
            try:
                _warm_agents()
                log_event("ui.warmup_done")
            except Exception as exc:
                log_event("ui.warmup_error", error=repr(exc))

        self._loop.call_soon_threadsafe(_warm)

        self._voice = VoiceLoop(self._loop)
        self._loop.call_soon_threadsafe(self._voice.start)
        self._loop.call_soon_threadsafe(scheduler().start)

        if get_settings().dashboard_enabled:
            dashboard().start()

        if get_settings().ui_bridge_enabled:
            bridge = ui_bridge()
            bridge.set_command_handler(self._voice.handle_ui_command)
            self._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(bridge.start())
            )

        try:
            from mira.ui.media_keys import install as _install_media_keys
            _install_media_keys(self._loop)
        except Exception as exc:
            log_event("ui.media_keys_boot_error", error=repr(exc))

        log_event("daemon.started")

        # Main-thread idle until a signal arrives. The asyncio thread does
        # all the work; we just need to keep the process alive and react to
        # termination requests.
        def _handle_signal(signum: int, _frame: Any) -> None:
            log_event("ui.signal_received", signal=signum)
            self._stop_event.set()

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=1.0)
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        log_event("ui.quit_requested")
        if self._voice is not None:
            self._voice.stop()
        if self._loop is not None:
            # Drain async resources on the loop thread before stopping it.
            # Each close is wrapped so one failure doesn't block the others —
            # a lingering Chromium is still worth avoiding even if httpx
            # fails to close, and vice versa.
            async def _shutdown_async() -> None:
                from mira.tools.web_tools import shutdown_http_client
                from mira.web.retrieval import shutdown_crawler

                for closer, label in (
                    (browser().close, "browser"),
                    (shutdown_crawler, "crawl4ai"),
                    (shutdown_http_client, "http_client"),
                ):
                    try:
                        await closer()
                    except Exception as exc:
                        log_event(f"ui.{label}_close_error", error=repr(exc))

            try:
                fut = asyncio.run_coroutine_threadsafe(
                    _shutdown_async(), self._loop
                )
                fut.result(timeout=5.0)
            except Exception as exc:
                log_event("ui.shutdown_error", error=repr(exc))
            self._loop.call_soon_threadsafe(self._loop.stop)
        log_event("daemon.stopped")


_INSTANCE_LOCK_SOCKET: Any = None


def _acquire_instance_lock() -> bool:
    """Bind a loopback socket to guarantee single-instance. If another
    MIRA is already running, bind fails → returns False. The socket is
    kept as a module-level reference so the kernel holds the port for
    the daemon's whole lifetime; nothing ever connects to it.
    """
    global _INSTANCE_LOCK_SOCKET
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 17657))
        sock.listen(1)
        _INSTANCE_LOCK_SOCKET = sock
        return True
    except OSError:
        return False


def run_app() -> None:
    MiraDaemon().run()
