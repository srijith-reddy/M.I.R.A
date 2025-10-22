# amma/utils/bgloop.py
import asyncio
from threading import Thread
import concurrent.futures

class BgLoop:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def run(self, coro, timeout: float | None = None):
        """Run an async coroutine in the background loop and wait for result."""
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            return None

    def call_soon(self, cb, *args, **kwargs):
        self.loop.call_soon_threadsafe(cb, *args, **kwargs)
