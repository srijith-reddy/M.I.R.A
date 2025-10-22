import asyncio
import nest_asyncio
from mira.agents.browser_agent import BrowserAgent

# allow nested event loops (Jupyter, notebooks, threaded contexts)
nest_asyncio.apply()


def run_async(coro):
    """Run async coroutines safely in both sync & async contexts."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Already inside an event loop → submit coroutine to it
        return asyncio.run_coroutine_threadsafe(coro, loop).result()
    else:
        return asyncio.run(coro)


class BrowserWorker:
    """
    Thin wrapper around BrowserAgent.
    Provides sync-friendly methods for testing/notebooks,
    while delegating to BrowserAgent's async methods.
    """

    def __init__(self, headless: bool = False, always_browser: bool = False):
        """
        :param headless: If False, Playwright/BrowserSession shows a real window.
        :param always_browser: If True, every call uses a real browser tab.
                               If False, browser opens only when query type forces it.
        """
        self.agent = BrowserAgent(headless=headless)
        self.always_browser = always_browser

    # ---------------------------
    # Async passthroughs
    # ---------------------------
    async def search(self, query: str, max_sites: int = 5):
        return await self.agent.search(query, max_sites)

    async def smart_extract(self, query: str, url: str, stateful: bool = False):
        # force browser if always_browser is enabled
        return await self.agent.smart_extract(query, url, stateful=(self.always_browser or stateful))

    # ---------------------------
    # ✅ Sync wrappers
    # ---------------------------
    def search_sync(self, query: str, max_sites: int = 5):
        return run_async(self.search(query, max_sites))

    def smart_extract_sync(self, query: str, url: str, stateful: bool = False):
        return run_async(self.smart_extract(query, url, stateful=stateful))

    def browser_use_get_sync(self, url: str):
        """Explicitly open a tab in browser-use."""
        return run_async(self.agent.browser_use_get(url))
