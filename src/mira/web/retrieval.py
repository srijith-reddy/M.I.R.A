from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from mira.obs.logging import log_event

# web_tools is imported lazily inside functions. Reason: `mira.tools.__init__`
# imports `research_tools` which imports this module. Top-level importing
# `mira.tools.web_tools` here would re-enter that partially-initialized
# package and trip ImportError. Deferring the import to call time sidesteps
# the cycle without restructuring the tools package.

# When article extraction returns text this short, the page is almost
# certainly a JavaScript-rendered shell (SPA) or blocked by anti-bot. Falling
# back to a JS-rendering path is the only way to content.
_JS_FALLBACK_THRESHOLD_CHARS = 400

# Per-research-call cap on JS-rendering fallbacks. Each render is 1-3s and
# holds a shared browser lock — we'd rather return a partial but fast answer
# than tie up browsers for 10s+ chasing every dead URL.
_MAX_JS_FALLBACKS_PER_CALL = 3

# Shared Crawl4AI crawler. Starting its playwright browser costs ~1-2s,
# so we amortize that across every research call in the session. It's not
# the same Chromium as mira.browser.runtime (which owns the user-visible
# browser for the Browser agent) — Crawl4AI runs its own headless instance
# with built-in anti-bot evasion and adaptive waits.
_crawler: Any | None = None
_crawler_lock: asyncio.Lock | None = None
_crawler_failed = False


def _get_crawler_lock() -> asyncio.Lock:
    global _crawler_lock
    if _crawler_lock is None:
        _crawler_lock = asyncio.Lock()
    return _crawler_lock


async def _get_crawler() -> Any | None:
    """Lazy, process-wide Crawl4AI singleton. Returns None if the library
    isn't installed or the browser launch failed — callers then skip the
    Crawl4AI tier and fall straight to Playwright."""
    global _crawler, _crawler_failed
    if _crawler is not None:
        return _crawler
    if _crawler_failed:
        return None
    async with _get_crawler_lock():
        if _crawler is not None:
            return _crawler
        if _crawler_failed:
            return None
        try:
            from crawl4ai import AsyncWebCrawler, BrowserConfig

            # Headless + verbose=False → Crawl4AI won't print its own banner
            # into our structured log stream. The playwright install that
            # ships with MIRA already has Chromium, so Crawl4AI reuses it.
            crawler = AsyncWebCrawler(
                config=BrowserConfig(headless=True, verbose=False)
            )
            await crawler.start()
            _crawler = crawler
            log_event("research.crawl4ai.ready")
            return _crawler
        except Exception as exc:
            _crawler_failed = True
            log_event("research.crawl4ai.unavailable", error=repr(exc))
            return None


@dataclass
class FetchOutcome:
    url: str
    title: str
    text: str
    mode: str
    via: str                    # "httpx" | "crawl4ai" | "playwright" | "failed"
    source_rank: int


async def _crawl4ai_fetch(url: str) -> tuple[str, str] | None:
    """Crawl4AI tier: JS rendering + anti-bot + content filtering. Returns
    (text, title) on success, None on any failure. Waits for network idle
    so SPA content is actually present when we extract. We run Crawl4AI's
    own extraction *and* trafilatura over the rendered HTML, picking
    whichever produces more content — Crawl4AI's markdown keeps heading
    structure but trafilatura occasionally catches body text that
    Crawl4AI's filters prune."""
    crawler = await _get_crawler()
    if crawler is None:
        return None
    try:
        from crawl4ai import CacheMode, CrawlerRunConfig

        # `networkidle` + page_timeout=12s → SPAs get a real chance to
        # hydrate. Cache is handled by our own diskcache layer upstream,
        # so tell Crawl4AI to bypass its internal one.
        cfg = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            wait_until="networkidle",
            page_timeout=12_000,
            verbose=False,
        )
        result = await asyncio.wait_for(
            crawler.arun(url=url, config=cfg), timeout=15.0,
        )
    except Exception as exc:
        log_event("research.crawl4ai.error", url=url, error=repr(exc))
        return None

    if not getattr(result, "success", False):
        log_event(
            "research.crawl4ai.unsuccessful",
            url=url,
            status=getattr(result, "status_code", None),
        )
        return None

    # Prefer Crawl4AI's own markdown (preserves headings), fall back to
    # trafilatura over the rendered HTML if markdown is thin.
    md_obj = getattr(result, "markdown", None)
    md_text = ""
    if md_obj is not None:
        # Crawl4AI ≥0.4 returns a MarkdownGenerationResult; older returns str.
        md_text = getattr(md_obj, "raw_markdown", None) or str(md_obj) or ""
    title = (getattr(result, "metadata", None) or {}).get("title") or ""

    html = getattr(result, "cleaned_html", "") or getattr(result, "html", "") or ""
    traf_text = ""
    if html:
        from mira.tools.web_tools import _trafilatura_extract

        traf_text, traf_title = _trafilatura_extract(html, url)
        title = title or traf_title

    text = md_text if len(md_text) >= len(traf_text) else traf_text
    text = (text or "").strip()
    if not text:
        return None
    return (text, title or "")


async def _playwright_fetch(url: str) -> tuple[str, str] | None:
    """Last-resort tier: the user-owned browser runtime. Uses a persistent
    profile (cookies, stored logins) which sometimes succeeds where a fresh
    headless context fails — useful for sites tied to a logged-in session."""
    try:
        from mira.browser.runtime import browser
    except Exception:
        return None

    async def _grab(page: Any) -> tuple[str, str] | None:
        try:
            await page.goto(url, wait_until="networkidle", timeout=10_000)
        except Exception as exc:
            log_event("research.playwright.nav_error", url=url, error=repr(exc))
            # Even on timeout, try to pull whatever rendered — partial content
            # is often enough for the reranker to find a relevant chunk.
        try:
            html = await page.content()
            title = await page.title()
        except Exception as exc:
            log_event("research.playwright.content_error", url=url, error=repr(exc))
            return None
        from mira.tools.web_tools import _trafilatura_extract

        text, meta_title = _trafilatura_extract(html, url)
        return (text or "", meta_title or title or "")

    try:
        runtime = browser()
        result = await asyncio.wait_for(
            runtime.run_with_recovery(_grab, tool_name="research.playwright"),
            timeout=14.0,
        )
    except Exception as exc:
        log_event("research.playwright.error", url=url, error=repr(exc))
        return None
    return result


async def _fetch_one_progressive(
    url: str, source_rank: int, *, js_budget: list[int],
) -> FetchOutcome | None:
    """Try the three retrieval tiers in order: httpx (cheap), Crawl4AI
    (JS+stealth), Playwright (logged-in profile). Stops at the first tier
    that yields meaningful content."""
    # --- Tier 1: httpx + trafilatura (already disk-cached upstream) -------
    from mira.tools.web_tools import FetchArgs, web_fetch

    try:
        out = await web_fetch(FetchArgs(url=url, mode="article"))
    except Exception as exc:
        log_event("research.httpx.error", url=url, error=repr(exc))
        out = {"ok": False}

    httpx_text = (out.get("text") or "") if isinstance(out, dict) else ""
    httpx_title = (out.get("title") or "") if isinstance(out, dict) else ""
    httpx_mode = (out.get("mode") or "article") if isinstance(out, dict) else "article"
    httpx_ok = bool(out.get("ok")) if isinstance(out, dict) else False

    if httpx_ok and len(httpx_text) >= _JS_FALLBACK_THRESHOLD_CHARS:
        return FetchOutcome(
            url=url, title=httpx_title, text=httpx_text,
            mode=httpx_mode, via="httpx", source_rank=source_rank,
        )

    # Budget gate — every further tier is expensive.
    if js_budget[0] <= 0:
        if httpx_text:
            return FetchOutcome(
                url=url, title=httpx_title, text=httpx_text,
                mode=httpx_mode, via="httpx", source_rank=source_rank,
            )
        return None
    js_budget[0] -= 1

    # --- Tier 2: Crawl4AI (headless JS + anti-bot + content filtering) ----
    c4 = await _crawl4ai_fetch(url)
    if c4 and c4[0] and len(c4[0]) > len(httpx_text):
        return FetchOutcome(
            url=url, title=c4[1] or httpx_title, text=c4[0],
            mode="article", via="crawl4ai", source_rank=source_rank,
        )

    # --- Tier 3: user-profile Playwright (last resort) --------------------
    pw = await _playwright_fetch(url)
    if pw and pw[0] and len(pw[0]) > len(httpx_text):
        return FetchOutcome(
            url=url, title=pw[1] or httpx_title, text=pw[0],
            mode="article", via="playwright", source_rank=source_rank,
        )

    if httpx_text:
        return FetchOutcome(
            url=url, title=httpx_title, text=httpx_text,
            mode=httpx_mode, via="httpx", source_rank=source_rank,
        )
    return None


async def progressive_fetch(
    urls: list[str], *,
    max_js_fallbacks: int = _MAX_JS_FALLBACKS_PER_CALL,
) -> list[FetchOutcome]:
    """Fetch every URL in parallel through the tiered strategy. Shared JS
    budget prevents a page full of anti-bot URLs from eating the whole
    latency allowance on render fallbacks."""
    js_budget = [max_js_fallbacks]
    tasks = [
        _fetch_one_progressive(u, i, js_budget=js_budget)
        for i, u in enumerate(urls)
    ]
    raw = await asyncio.gather(*tasks, return_exceptions=True)
    return [item for item in raw if isinstance(item, FetchOutcome)]


async def shutdown_crawler() -> None:
    """Close the shared Crawl4AI browser. Safe to call even if never started.
    Hook into app shutdown so the Chromium process doesn't leak."""
    global _crawler
    if _crawler is None:
        return
    try:
        await _crawler.close()
    except Exception as exc:
        log_event("research.crawl4ai.close_error", error=repr(exc))
    _crawler = None
