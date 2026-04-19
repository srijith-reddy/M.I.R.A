from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Awaitable, Callable

from mira.obs.logging import log_event

Handler = Callable[[str, dict[str, Any]], Awaitable[None] | None]


class EventBus:
    """In-process async pub/sub bus.

    All cross-component signals (wake.triggered, speech.started/ended, tts.*,
    turn.completed, tool.call, etc.) flow through here. Subscribers may be
    sync or async; async handlers are awaited, sync handlers are called inline.

    Contract:
      * Publish is non-blocking from the caller's perspective when using
        `publish_nowait`; `publish` awaits all handlers in parallel.
      * A handler raising does not abort sibling handlers — errors are logged
        and swallowed so one misbehaving subscriber can't take down the bus.
      * Every published event is mirrored to the structured log so we always
        have a durable record of signal flow for evals and debugging.
    """

    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        self._subs[topic].append(handler)

        def _unsub() -> None:
            try:
                self._subs[topic].remove(handler)
            except ValueError:
                pass

        return _unsub

    async def publish(self, topic: str, **payload: Any) -> None:
        log_event(f"bus.{topic}", **payload)
        handlers = list(self._subs.get(topic, ()))
        if not handlers:
            return
        coros = []
        for h in handlers:
            try:
                result = h(topic, payload)
            except Exception as exc:
                log_event("bus.handler_error", topic=topic, error=repr(exc))
                continue
            if asyncio.iscoroutine(result):
                coros.append(result)
        if coros:
            results = await asyncio.gather(*coros, return_exceptions=True)
            for r in results:
                if isinstance(r, BaseException):
                    log_event("bus.handler_error", topic=topic, error=repr(r))

    def publish_nowait(self, topic: str, **payload: Any) -> None:
        """Fire-and-forget from sync code. Schedules async dispatch if a loop is running,
        otherwise just logs the event (no subscribers will fire in that case).
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log_event(f"bus.{topic}", **payload)
            return
        loop.create_task(self.publish(topic, **payload))


_bus: EventBus | None = None


def bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
