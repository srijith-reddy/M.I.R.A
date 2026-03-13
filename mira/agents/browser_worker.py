# ======================================
# mira/agents/browser_worker.py (final optimized)
# ======================================
import asyncio
import nest_asyncio
from mira.agents.browser_agent import BrowserAgent

# Allow nested loops (Jupyter, REPLs, threaded orchestrators)
nest_asyncio.apply()


def run_async(coro):
    """Run async coroutines safely in both sync & async contexts."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Already inside event loop (e.g. FastAPI, notebook)
        return asyncio.run_coroutine_threadsafe(coro, loop).result()
    else:
        return asyncio.run(coro)


class BrowserWorker:
    """
    Thin sync-friendly wrapper around BrowserAgent.
    Supports async calls while keeping sync APIs for graph nodes.
    """

    def __init__(self, headless: bool = False, always_browser: bool = False):
        """
        :param headless: If False, opens real browser windows (debug use).
        :param always_browser: If True, always forces Playwright for every call.
        """
        self.agent = BrowserAgent(headless=headless)
        self.always_browser = always_browser

    # ======================================================
    # 🔹 Async passthroughs
    # ======================================================
    async def search(self, query: str, max_sites: int = 5):
        return await self.agent.search(query, max_sites)

    async def smart_extract(self, query: str, url: str, stateful: bool = False):
        return await self.agent.smart_extract(query, url, stateful=(self.always_browser or stateful))

    async def discover(self, query: str):
        """Async passthrough to BrowserAgent.discover()"""
        return await self.agent.discover(query)

    async def handle(self, payload: dict):
        """Async passthrough to BrowserAgent.handle()"""
        return await self.agent.handle(payload)

    # ======================================================
    # ✅ Sync wrappers
    # ======================================================
    def search_sync(self, query: str, max_sites: int = 5):
        return run_async(self.search(query, max_sites))

    def smart_extract_sync(self, query: str, url: str, stateful: bool = False):
        return run_async(self.smart_extract(query, url, stateful=stateful))

    def discover_sync(self, query: str):
        """Synchronous discover wrapper — trust-ranked, GPT-4o summarized."""
        return run_async(self.discover(query))

    def handle_sync(self, payload: dict):
        """Synchronous handle wrapper (Planner / Buying compatibility)."""
        return run_async(self.handle(payload))

    def browser_use_get_sync(self, url: str):
        """Explicitly open a tab in BrowserUse."""
        return run_async(self.agent.browser_use_get(url))
