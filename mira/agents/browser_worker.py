# ======================================
# mira/agents/browser_worker.py (final)
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

    # ---------------------------
    # Async passthroughs
    # ---------------------------
    async def search(self, query: str, max_sites: int = 5):
        return await self.agent.search(query, max_sites)

    async def smart_extract(self, query: str, url: str, stateful: bool = False):
        return await self.agent.smart_extract(query, url, stateful=(self.always_browser or stateful))

    async def multi_site_answer(self, query: str, urls: list[str]):
        return await self.agent.multi_site_answer(query, urls)

    # ---------------------------
    # ✅ Sync wrappers
    # ---------------------------
    def search_sync(self, query: str, max_sites: int = 5):
        return run_async(self.search(query, max_sites))

    def smart_extract_sync(self, query: str, url: str, stateful: bool = False):
        return run_async(self.smart_extract(query, url, stateful=stateful))

    def multi_site_answer_sync(self, query: str, urls: list[str]):
        return run_async(self.multi_site_answer(query, urls))

    def browser_use_get_sync(self, url: str):
        """Explicitly open a tab in browser-use."""
        return run_async(self.agent.browser_use_get(url))
