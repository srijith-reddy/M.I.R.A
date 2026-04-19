from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from mira.config.paths import paths
from mira.config.settings import get_settings
from mira.obs.logging import log_event
from mira.runtime.registry import registry, tool
from mira.safety.domains import is_trusted, tag_and_sort

_installed = False

_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

# Shared HTTP client — amortizes TCP/TLS setup across every call. httpx's
# AsyncClient is safe for concurrent use; the connection pool reuses
# keep-alive sockets to Brave and to arbitrary fetch targets.
_HTTP_CLIENT = None  # type: ignore[var-annotated]


def _client():  # type: ignore[no-untyped-def]
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        import httpx
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/128.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
    return _HTTP_CLIENT


async def shutdown_http_client() -> None:
    """Close the shared httpx client on process exit. Safe to call when never
    initialized. Called from the menubar quit handler so TCP/TLS sockets
    get cleaned up deterministically instead of leaking until GC."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        return
    try:
        await _HTTP_CLIENT.aclose()
    except Exception as exc:
        log_event("web.http_client.close_error", error=repr(exc))
    _HTTP_CLIENT = None


# Persistent 24h fetch cache. Same URL is hit repeatedly across turns and
# sessions — news follow-ups, research threads, "read me that article
# again." diskcache is process-safe, survives restarts, and auto-evicts
# oldest entries past the size cap. Keyed by (requested_mode, url) so a
# later "article" request doesn't accidentally reuse a "text" fallback.
# Full pre-truncation text is stored; we re-apply `max_chars` on read so
# varying limits don't fragment the cache.
_FETCH_CACHE_TTL_S = 24 * 3600
_FETCH_CACHE_SIZE_LIMIT = 256 * 1024 * 1024  # 256 MiB on disk
_fetch_cache = None  # type: ignore[var-annotated]


def _fetch_cache_handle():  # type: ignore[no-untyped-def]
    global _fetch_cache
    if _fetch_cache is None:
        try:
            import diskcache
        except Exception:
            return None
        cache_dir = paths.cache_dir / "web-fetch"
        cache_dir.mkdir(parents=True, exist_ok=True)
        _fetch_cache = diskcache.Cache(
            str(cache_dir), size_limit=_FETCH_CACHE_SIZE_LIMIT
        )
    return _fetch_cache


# 60s TTL in-memory cache. Same-turn repeats are common ("warriors
# tonight" → follow-ups about the same game) and Brave bills per call.
# Keyed by (query, trust_mode, max_results). Not persisted — a crash or
# restart wipes it, which is fine.
_CACHE_TTL_SECS = 60.0
_cache: dict[tuple[str, str, int], tuple[float, dict[str, Any]]] = {}


class WebSearchArgs(BaseModel):
    query: str
    max_results: int = Field(default=5, ge=1, le=10)
    trust_mode: Literal[
        "off", "default", "strict", "news", "commerce", "booking", "reference"
    ] = "default"


def install_web_tools() -> None:
    """Register optional web tools. No-op when the required keys are missing —
    this lets us ship the code and let users opt in with a single env var
    without an import-time crash. Idempotent."""
    global _installed
    if _installed:
        return
    settings = get_settings()
    if settings.brave_search_api_key and registry().get("web.search") is None:
        _register_brave()
    _installed = True


def _summarize_search(data: Any, *, max_items: int = 6) -> str:
    if not isinstance(data, dict):
        return str(data)
    if not data.get("ok", True):
        return f"search error: {data.get('error') or 'unknown'}"
    results = data.get("results") or []
    if not results:
        return f"no results for '{data.get('query', '')}'"
    lines: list[str] = []
    for r in results[:max_items]:
        title = (r.get("title") or "").strip()
        url = r.get("url") or ""
        tier = r.get("trust_tier") or ""
        snippet = (r.get("snippet") or "").strip()
        if len(snippet) > 200:
            snippet = snippet[:200] + "..."
        tier_tag = f"[{tier}] " if tier and tier != "unknown" else ""
        lines.append(f"- {tier_tag}{title} — {url}\n  {snippet}")
    if len(results) > max_items:
        lines.append(f"...+{len(results) - max_items} more results")
    return "\n".join(lines)


def _summarize_fetch(data: Any) -> str:
    if not isinstance(data, dict):
        return str(data)
    if not data.get("ok"):
        return f"fetch error: {data.get('error') or 'unknown'}"
    # Article mode already returns clean main-content text. The summarizer's
    # job is only to strip the surrounding payload — the LLM benefits from
    # the body text itself untouched.
    title = (data.get("title") or "").strip()
    text = (data.get("text") or "").strip()
    url = data.get("url") or ""
    head = f"{title}\n{url}\n\n" if title else f"{url}\n\n"
    return head + text


def _register_brave() -> None:
    @tool(
        "web.search",
        description=(
            "Fast JSON web search via Brave Search. Returns titles, URLs, and "
            "short snippets. Results are tagged with trust tiers; prefer the "
            "tier1/tier2 entries at the top of the list. Use `trust_mode` to "
            "bias the ranking: `news` for current events, `commerce` for "
            "shopping, `booking` for travel/tickets, `reference` for docs."
        ),
        params=WebSearchArgs,
        tags=("web",),
        summarizer=_summarize_search,
        volatile=True,
    )
    async def web_search(args: WebSearchArgs) -> dict[str, Any]:
        cache_key = (args.query.strip().lower(), args.trust_mode, args.max_results)
        now = time.time()
        hit = _cache.get(cache_key)
        if hit and now - hit[0] < _CACHE_TTL_SECS:
            log_event("web.search.cache_hit", query=args.query)
            return hit[1]

        settings = get_settings()
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": settings.brave_search_api_key or "",
        }
        params = {"q": args.query, "count": args.max_results}

        # Retry once on 429 with a short backoff. A single retry is enough —
        # if Brave is rate-limiting us persistently, sitting in a loop helps
        # nobody; the caller should fall back to `browser.search_google`.
        data: dict[str, Any] | None = None
        last_error: str | None = None
        client = _client()
        for attempt in range(2):
            if True:
                try:
                    resp = await client.get(
                        _BRAVE_ENDPOINT, headers=headers, params=params
                    )
                except Exception as exc:
                    last_error = repr(exc)
                    log_event("web.search.error", error=last_error, attempt=attempt)
                    break

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After") or 1.0)
                retry_after = min(retry_after, 2.0)
                log_event("web.search.rate_limited", retry_after=retry_after)
                if attempt == 0:
                    await asyncio.sleep(retry_after)
                    continue
                return {
                    "query": args.query,
                    "trust_mode": args.trust_mode,
                    "results": [],
                    "ok": False,
                    "error": "rate limited — try browser.search_google",
                }
            if resp.status_code >= 400:
                last_error = f"http {resp.status_code}"
                log_event("web.search.http_error", status=resp.status_code)
                break
            data = resp.json()
            break

        if data is None:
            return {
                "query": args.query,
                "trust_mode": args.trust_mode,
                "results": [],
                "ok": False,
                "error": last_error or "search failed",
            }

        raw_results = ((data.get("web") or {}).get("results") or [])[: args.max_results]
        results = []
        for r in raw_results:
            # Brave returns a `thumbnail` object with `src` and `original`;
            # we prefer `src` (CDN-cached, small) since it renders in a 44px
            # HUD row. Falls back to None and the UI just skips the image.
            thumb_obj = r.get("thumbnail") or {}
            thumbnail = thumb_obj.get("src") if isinstance(thumb_obj, dict) else None
            results.append(
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": (r.get("description") or "")[:400],
                    "thumbnail": thumbnail,
                }
            )

        kept, _ = tag_and_sort(results, args.trust_mode, drop_denied=False)
        denied = [r for r in kept if r["trust_tier"] == "denied"]
        if denied:
            log_event(
                "web.search.denied",
                query=args.query,
                mode=args.trust_mode,
                domains=[r["trust_domain"] for r in denied],
            )
        out = {
            "query": args.query,
            "trust_mode": args.trust_mode,
            "results": kept,
            "ok": True,
        }
        _cache[cache_key] = (now, out)
        # Opportunistic GC — keep the cache small.
        if len(_cache) > 64:
            cutoff = now - _CACHE_TTL_SECS
            for k, (ts, _v) in list(_cache.items()):
                if ts < cutoff:
                    _cache.pop(k, None)
        return out


# ---------- Static fetcher (no browser) ----------


class FetchArgs(BaseModel):
    url: str = Field(..., description="Absolute URL to fetch.")
    mode: Literal["article", "text", "raw"] = Field(
        default="article",
        description=(
            "'article' runs trafilatura for clean main-content extraction "
            "(best for news/blogs/docs). 'text' returns all visible text via "
            "selectolax. 'raw' returns the untouched HTML (capped)."
        ),
    )
    max_chars: int = Field(default=8000, ge=500, le=20000)


@tool(
    "web.fetch",
    description=(
        "Fetch a URL without spinning up the browser. ~10x faster than "
        "browser.navigate+read_page for static pages. Uses trafilatura for "
        "article extraction. Falls back to selectolax text for non-articles. "
        "Refuses denylisted domains. Does NOT run JavaScript — for SPAs use "
        "browser.navigate instead."
    ),
    params=FetchArgs,
    tags=("web",),
    summarizer=_summarize_fetch,
)
async def web_fetch(args: FetchArgs) -> dict[str, Any]:
    verdict = is_trusted(args.url, "default")
    if verdict.tier == "denied":
        return {
            "ok": False,
            "error": f"domain denylisted: {verdict.domain}",
            "trust_domain": verdict.domain,
        }

    cache = _fetch_cache_handle()
    cache_key = f"{args.mode}:{args.url}"
    if cache is not None:
        try:
            hit = cache.get(cache_key)
        except Exception:
            hit = None
        if isinstance(hit, dict) and hit.get("text_full") is not None:
            log_event("web.fetch.cache_hit", url=args.url, mode=args.mode)
            full_text = hit["text_full"]
            out: dict[str, Any] = {
                "ok": True,
                "url": hit.get("url") or args.url,
                "text": full_text[: args.max_chars],
                "chars": min(len(full_text), args.max_chars),
                "mode": hit.get("mode_used") or args.mode,
                "cached": True,
            }
            if hit.get("title"):
                out["title"] = hit["title"]
            return out

    # `Accept: text/html` is site-specific — merged per-call rather than in
    # the shared client, which is used by both JSON (Brave) and HTML fetches.
    try:
        resp = await _client().get(
            args.url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
    except Exception as exc:
        log_event("web.fetch.error", url=args.url, error=repr(exc))
        return {"ok": False, "error": f"fetch failed: {exc}"}

    if resp.status_code >= 400:
        return {
            "ok": False,
            "error": f"http {resp.status_code}",
            "url": str(resp.url),
        }
    html = resp.text

    resolved_url = str(resp.url)

    if args.mode == "raw":
        _cache_fetch(cache, cache_key, url=resolved_url, text_full=html, title="", mode_used="raw")
        return {
            "ok": True, "url": resolved_url,
            "text": html[: args.max_chars],
            "chars": min(len(html), args.max_chars),
            "mode": "raw",
        }

    if args.mode == "article":
        text, title = await asyncio.to_thread(_trafilatura_extract, html, resolved_url)
        if text:
            _cache_fetch(cache, cache_key, url=resolved_url, text_full=text, title=title, mode_used="article")
            return {
                "ok": True, "url": resolved_url,
                "title": title,
                "text": text[: args.max_chars],
                "chars": min(len(text), args.max_chars),
                "mode": "article",
            }
        # Fall through to plain text if article extraction is empty
        # (happens on home pages, listings, non-article pages).

    text = await asyncio.to_thread(_selectolax_text, html)
    _cache_fetch(cache, cache_key, url=resolved_url, text_full=text, title="", mode_used="text")
    return {
        "ok": True, "url": resolved_url,
        "text": text[: args.max_chars],
        "chars": min(len(text), args.max_chars),
        "mode": "text",
    }


def _cache_fetch(cache, key: str, *, url: str, text_full: str, title: str, mode_used: str) -> None:
    if cache is None or not text_full:
        return
    try:
        cache.set(
            key,
            {"url": url, "text_full": text_full, "title": title, "mode_used": mode_used},
            expire=_FETCH_CACHE_TTL_S,
        )
    except Exception as exc:
        log_event("web.fetch.cache_write_error", error=repr(exc))


def _trafilatura_extract(html: str, url: str) -> tuple[str, str]:
    """Main-content extraction. Trafilatura is the current SOTA open-source
    article extractor — consistently beats readability on benchmarks, handles
    paywalls/ads/nav noise, and returns structured text."""
    try:
        import trafilatura
    except Exception:
        return "", ""
    try:
        text = trafilatura.extract(
            html, url=url,
            include_comments=False, include_tables=True,
            favor_recall=True,
        ) or ""
        meta = trafilatura.extract_metadata(html) if text else None
        title = (meta.title if meta else "") or ""
    except Exception as exc:
        log_event("web.fetch.trafilatura_error", error=repr(exc))
        return "", ""
    return text.strip(), title.strip()


def _selectolax_text(html: str) -> str:
    """Fast whole-page visible-text extraction. ~5x faster than BeautifulSoup
    and keeps block-level spacing so the LLM sees paragraph breaks."""
    try:
        from selectolax.parser import HTMLParser
    except Exception:
        return ""
    try:
        tree = HTMLParser(html)
        for sel in ("script", "style", "noscript", "template", "nav", "footer", "header", "aside"):
            for node in tree.css(sel):
                node.decompose()
        body = tree.body or tree.root
        if body is None:
            return ""
        return body.text(separator="\n", strip=True)
    except Exception as exc:
        log_event("web.fetch.selectolax_error", error=repr(exc))
        return ""
