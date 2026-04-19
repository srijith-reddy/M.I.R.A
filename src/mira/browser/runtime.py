from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, TypeVar

from mira.config.paths import paths
from mira.config.settings import get_settings
from mira.obs.logging import log_event
from mira.runtime.tracing import span


T = TypeVar("T")


# Exception names we treat as "browser crashed, try to recover". We match on
# name rather than `isinstance` because playwright's exception hierarchy
# shifts between versions and the import adds a hard dep at module load.
_CRASH_ERROR_NAMES = frozenset(
    {
        "TargetClosedError",
        "BrowserClosedError",
        "BrowserContextClosedError",
        "Error",  # Playwright's base Error — gated further by the message.
    }
)

_CRASH_MESSAGE_HINTS = (
    "target closed",
    "browser has been closed",
    "browser closed unexpectedly",
    "context was closed",
    "page was closed",
    "connection closed",
    "crashed",
)


def _looks_like_crash(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name not in _CRASH_ERROR_NAMES:
        return False
    msg = str(exc).lower()
    # Playwright wraps lots of transient errors in `Error`; only recover when
    # the message is actually about a dead connection/target. Otherwise we'd
    # swallow e.g. selector-not-found and retry pointlessly.
    if name == "Error":
        return any(hint in msg for hint in _CRASH_MESSAGE_HINTS)
    return True


# Launch args that quietly improve our success rate on anti-bot sites
# without crossing into stealth-plugin territory. Each one is documented
# because this is the first place to debug when a site starts blocking us.
_LAUNCH_ARGS = (
    # Removes the `navigator.webdriver=true` flag. Dead giveaway otherwise.
    "--disable-blink-features=AutomationControlled",
    # Prevents Chrome from showing the "automated test software" info bar.
    "--disable-infobars",
    # Reduces the chance of being flagged by heuristics that key on these.
    "--no-default-browser-check",
    "--no-first-run",
)


# Realistic Chrome 128/macOS UA. Using the real Playwright-bundled Chromium
# version leaks a recognizable string; overriding with a current stable
# Chrome UA is what most anti-bot libraries key on.
_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

_DEFAULT_EXTRA_HEADERS = {
    # Anti-bot fingerprinters flag missing/odd Accept-Language.
    "Accept-Language": "en-US,en;q=0.9",
}


class BrowserRuntime:
    """Single persistent Chromium context shared across all browser tool calls.

    Why persistent, not launch-per-turn:
      * Logins, cookies, and site state survive across turns — the user says
        "check my gmail" once, and every subsequent turn benefits.
      * Cold start of Playwright is ~2s; amortizing that across a long-lived
        MIRA process is the difference between "instant" and "sluggish".

    Concurrency: we serialize access to the shared page behind an asyncio
    lock. Two browser tool calls on the same page would race DOM changes;
    if we ever need real parallelism we'll spawn ephemeral pages — but this
    is not that day.

    Resilience: Chromium can die for many reasons — OOM, tab crash, a site
    that triggers GPU-process hangs. On a detected crash we tear the runtime
    down, rebuild, and retry the pending operation once. A second failure
    bubbles up to the tool layer so the LLM can report something useful
    instead of silently looping.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._playwright: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None
        self._lock = asyncio.Lock()
        # Snapshot telemetry — useful for diagnosing a noisy site or a
        # flaky machine. Surfaced via `stats()` for the `--doctor` flow.
        self._restarts = 0
        self._last_launch_ts: float | None = None

    async def ensure_started(self) -> None:
        if self._context is not None and await self._is_context_alive():
            return
        if self._context is not None:
            # We're here because a prior context died. Tear down before
            # rebuilding so we don't leak a subprocess.
            await self._teardown(reason="dead context at ensure_started")
        await self._launch()

    async def _launch(self) -> None:
        with span("browser.launch", headless=self._settings.browser_headless):
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            paths.ensure()
            user_agent = self._settings.browser_user_agent or _DEFAULT_UA
            launch_kwargs: dict[str, Any] = {
                "user_data_dir": str(paths.browser_profile),
                "headless": self._settings.browser_headless,
                "viewport": {"width": 1280, "height": 800},
                "accept_downloads": True,
                "downloads_path": str(paths.downloads),
                "user_agent": user_agent,
                "args": list(_LAUNCH_ARGS),
                "extra_http_headers": dict(_DEFAULT_EXTRA_HEADERS),
                "locale": "en-US",
            }
            self._context = await self._playwright.chromium.launch_persistent_context(
                **launch_kwargs
            )
            # Conservative default timeout — a hung tab shouldn't block a
            # voice turn indefinitely. Individual tool calls can tighten.
            self._context.set_default_navigation_timeout(20000)
            self._context.set_default_timeout(10000)

            # Hook context-level close so we notice if Chromium dies while
            # we're not actively touching it. `_context_closed` flips a flag
            # that `_is_context_alive` consults instead of probing.
            self._context_closed = False
            self._context.on("close", self._on_context_close)

            existing = self._context.pages
            self._page = existing[0] if existing else await self._context.new_page()
            self._last_launch_ts = time.time()
            log_event(
                "browser.ready",
                user_data_dir=str(paths.browser_profile),
                pages=len(self._context.pages),
                restart=self._restarts,
                ua=user_agent,
            )

    def _on_context_close(self) -> None:
        # Runs on the playwright event callback; must stay sync. We only
        # flip a flag — actual teardown happens in the next `ensure_started`.
        self._context_closed = True
        log_event("browser.context_closed")

    async def _is_context_alive(self) -> bool:
        if self._context is None or getattr(self, "_context_closed", False):
            return False
        # A cheap probe: the page exists and isn't closed. We don't issue
        # a DOM call — that would add ~10ms to every tool dispatch and most
        # of the time the context is fine.
        if self._page is None:
            return False
        try:
            return not self._page.is_closed()
        except Exception:
            return False

    async def _teardown(self, *, reason: str) -> None:
        log_event("browser.teardown", reason=reason)
        if self._context is not None:
            try:
                await self._context.close()
            except Exception as exc:
                log_event("browser.teardown_context_error", error=repr(exc))
        self._context = None
        self._page = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as exc:
                log_event("browser.teardown_playwright_error", error=repr(exc))
        self._playwright = None
        self._context_closed = False

    async def _recover(self) -> None:
        """Hard reset: tear down whatever's left and relaunch."""
        self._restarts += 1
        await self._teardown(reason="recover")
        await self._launch()

    async def page(self) -> Any:
        """Return the shared main page, launching (or relaunching) as needed."""
        await self.ensure_started()
        if self._page is None or self._page.is_closed():
            # Context is alive but the page got closed (user triggered a
            # window close, site called window.close(), etc). Replace it
            # without a full restart.
            self._page = await self._context.new_page()
            log_event("browser.page_replaced")
        return self._page

    def lock(self) -> asyncio.Lock:
        return self._lock

    async def run_with_recovery(
        self,
        fn: Callable[[Any], Awaitable[T]],
        *,
        tool_name: str = "browser",
    ) -> T:
        """Run `fn(page)` inside the shared lock; on a Chromium crash, rebuild
        the context and retry once.

        Tools that want auto-recovery use this instead of calling `page()`
        directly. A second crash propagates — at that point something is
        structurally wrong (disk full, wrong arch binary, etc) and looping
        would just log noise."""
        async with self._lock:
            page = await self.page()
            try:
                return await fn(page)
            except BaseException as exc:
                if not _looks_like_crash(exc):
                    raise
                log_event(
                    "browser.crash_detected",
                    tool=tool_name,
                    error=repr(exc),
                    error_type=type(exc).__name__,
                )

            # First attempt crashed; recover and retry ONCE. We re-enter the
            # try/except so a second crash surfaces the original style of
            # error to the caller.
            await self._recover()
            page = await self.page()
            return await fn(page)

    async def export_storage_state(self, *, path: str | None = None) -> dict[str, Any]:
        """Snapshot cookies + localStorage for the current context.

        Persistent contexts already keep this state in `user_data_dir` on
        disk, so the export is mainly for backup or cross-machine migration
        (e.g. copying a login session to a dev box)."""
        await self.ensure_started()
        assert self._context is not None
        state = await self._context.storage_state(path=path)
        return state if isinstance(state, dict) else {}

    def stats(self) -> dict[str, Any]:
        return {
            "restarts": self._restarts,
            "last_launch_ts": self._last_launch_ts,
            "started": self._context is not None,
        }

    async def close(self) -> None:
        await self._teardown(reason="explicit close")
        log_event("browser.closed")


_runtime: BrowserRuntime | None = None


def browser() -> BrowserRuntime:
    global _runtime
    if _runtime is None:
        _runtime = BrowserRuntime()
    return _runtime
