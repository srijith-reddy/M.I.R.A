from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from mira.browser import browser, extract_clean_text
from mira.config.paths import paths
from mira.obs.logging import log_event
from mira.runtime.registry import tool
from mira.safety.domains import is_trusted, tag_and_sort

# ---------- param schemas ----------


class NavigateArgs(BaseModel):
    url: str = Field(..., description="Absolute URL to open.")
    wait_for: str | None = Field(
        default=None,
        description="Optional CSS selector to wait for after navigation.",
    )


class ReadPageArgs(BaseModel):
    selector: str | None = Field(
        default=None, description="Optional selector to scope extraction."
    )
    max_chars: int = Field(default=8000, ge=200, le=16000)


class ClickArgs(BaseModel):
    selector: str = Field(..., description="CSS selector for the element to click.")
    timeout_ms: int = Field(default=5000, ge=500, le=30000)


class FillArgs(BaseModel):
    selector: str
    value: str


class PressArgs(BaseModel):
    key: str = Field(..., description="A Playwright key name, e.g. 'Enter'.")


class ScreenshotArgs(BaseModel):
    path: str | None = Field(
        default=None,
        description="Optional output path. Defaults to a timestamped file in the data dir.",
    )
    full_page: bool = False


class SearchGoogleArgs(BaseModel):
    query: str
    max_results: int = Field(default=5, ge=1, le=10)


class ExportStateArgs(BaseModel):
    path: str | None = Field(
        default=None,
        description=(
            "Optional destination file for the storage_state JSON. "
            "Defaults to a timestamped file in the data dir."
        ),
    )


# ---------- summarizers ----------


def _summarize_read_page(data: Any) -> str:
    """Compact page dump: title + URL + first N chars of extracted text.
    The text body is usually what the planner wants verbatim, so we don't
    further compress it — summarization here is about stripping the JSON
    envelope, not rewriting."""
    if not isinstance(data, dict):
        return str(data)
    title = (data.get("title") or "").strip()
    url = data.get("url") or ""
    text = (data.get("text") or "").strip()
    header = f"{title} — {url}\n\n" if title else f"{url}\n\n"
    return header + text


def _summarize_search_google(data: Any, *, max_items: int = 6) -> str:
    if not isinstance(data, dict):
        return str(data)
    results = data.get("results") or []
    if not results:
        return f"no results for '{data.get('query', '')}'"
    lines: list[str] = []
    for r in results[:max_items]:
        title = (r.get("title") or "").strip()
        url = r.get("url") or ""
        tier = r.get("trust_tier") or ""
        tier_tag = f"[{tier}] " if tier and tier != "unknown" else ""
        lines.append(f"- {tier_tag}{title} — {url}")
    if len(results) > max_items:
        lines.append(f"...+{len(results) - max_items} more")
    return "\n".join(lines)


# ---------- tools ----------


@tool(
    "browser.navigate",
    description="Open a URL in the shared browser page. Read-only.",
    params=NavigateArgs,
    tags=("browser",),
)
async def browser_navigate(args: NavigateArgs) -> dict[str, Any]:
    # Trust gate: refuse denylisted domains at the tool boundary so the LLM
    # can't be talked into opening a known-bad URL. Unknown/tiered domains
    # pass through — denylist hits are the only hard block.
    verdict = is_trusted(args.url, "default")
    if verdict.tier == "denied":
        log_event("browser.navigate_denied", url=args.url, domain=verdict.domain)
        raise RuntimeError(
            f"refusing to open denylisted domain: {verdict.domain}"
        )

    rt = browser()

    async def _do(page: Any) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            await page.goto(args.url, wait_until="domcontentloaded", timeout=20000)
            if args.wait_for:
                try:
                    await page.wait_for_selector(args.wait_for, timeout=8000)
                except Exception as exc:
                    log_event(
                        "browser.wait_for_missed",
                        selector=args.wait_for,
                        error=repr(exc),
                    )
            title = await page.title()
            url = page.url
        except Exception as exc:
            raise RuntimeError(f"navigate failed: {exc}") from exc
        return {
            "url": url,
            "title": title,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        }

    return await rt.run_with_recovery(_do, tool_name="browser.navigate")


@tool(
    "browser.read_page",
    description="Return cleaned, bounded text from the current page.",
    params=ReadPageArgs,
    tags=("browser",),
    summarizer=_summarize_read_page,
)
async def browser_read_page(args: ReadPageArgs) -> dict[str, Any]:
    rt = browser()

    async def _do(page: Any) -> dict[str, Any]:
        text = await extract_clean_text(
            page, selector=args.selector, max_chars=args.max_chars
        )
        return {
            "url": page.url,
            "title": await page.title(),
            "text": text,
            "chars": len(text),
        }

    return await rt.run_with_recovery(_do, tool_name="browser.read_page")


@tool(
    "browser.click",
    description="Click an element by CSS selector. Side-effectful — requires confirmation.",
    params=ClickArgs,
    requires_confirmation=True,
    tags=("browser",),
)
async def browser_click(args: ClickArgs) -> dict[str, Any]:
    rt = browser()

    async def _do(page: Any) -> dict[str, Any]:
        try:
            await page.click(args.selector, timeout=args.timeout_ms)
        except Exception as exc:
            raise RuntimeError(f"click failed: {exc}") from exc
        return {"clicked": args.selector, "url": page.url}

    return await rt.run_with_recovery(_do, tool_name="browser.click")


@tool(
    "browser.fill",
    description="Type into an input. Inert until submitted — no confirmation needed.",
    params=FillArgs,
    tags=("browser",),
)
async def browser_fill(args: FillArgs) -> dict[str, Any]:
    rt = browser()

    async def _do(page: Any) -> dict[str, Any]:
        await page.fill(args.selector, args.value, timeout=5000)
        return {"filled": args.selector, "chars": len(args.value)}

    return await rt.run_with_recovery(_do, tool_name="browser.fill")


@tool(
    "browser.press",
    description="Press a keyboard key. Can submit forms — requires confirmation.",
    params=PressArgs,
    requires_confirmation=True,
    tags=("browser",),
    success_phrase="Okay.",
)
async def browser_press(args: PressArgs) -> dict[str, Any]:
    rt = browser()

    async def _do(page: Any) -> dict[str, Any]:
        await page.keyboard.press(args.key)
        return {"pressed": args.key, "url": page.url}

    return await rt.run_with_recovery(_do, tool_name="browser.press")


@tool(
    "browser.screenshot",
    description="Capture a PNG of the current page.",
    params=ScreenshotArgs,
    tags=("browser",),
)
async def browser_screenshot(args: ScreenshotArgs) -> dict[str, Any]:
    rt = browser()

    async def _do(page: Any) -> dict[str, Any]:
        target = (
            Path(args.path)
            if args.path
            else paths.data_dir / "screenshots" / f"shot-{int(time.time())}.png"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(target), full_page=args.full_page)
        return {"path": str(target), "artifacts": [str(target)]}

    return await rt.run_with_recovery(_do, tool_name="browser.screenshot")


@tool(
    "browser.search_google",
    description=(
        "Run a Google search in the shared browser and return the top result URLs + titles. "
        "Useful when a direct URL isn't known. Read-only."
    ),
    params=SearchGoogleArgs,
    tags=("browser",),
    summarizer=_summarize_search_google,
)
async def browser_search_google(args: SearchGoogleArgs) -> dict[str, Any]:
    rt = browser()

    async def _do(page: Any) -> dict[str, Any]:
        q = args.query.strip()
        await page.goto(
            f"https://www.google.com/search?q={_url_quote(q)}",
            wait_until="domcontentloaded",
            timeout=15000,
        )
        # Google changes DOM often; prefer a resilient query selector.
        results: list[dict[str, str]] = []
        try:
            locator = page.locator("a h3")
            count = min(await locator.count(), args.max_results)
            for i in range(count):
                h3 = locator.nth(i)
                title = (await h3.inner_text(timeout=2000)).strip()
                # Walk up to the anchor for the href.
                anchor = h3.locator("xpath=ancestor::a[1]")
                href = await anchor.get_attribute("href") or ""
                if title and href.startswith("http"):
                    results.append({"title": title, "url": href})
        except Exception as exc:
            log_event("browser.search_parse_error", error=repr(exc))
        # Tag with the same trust layer web.search uses so the LLM sees the
        # same signal whether it searched via Brave or scraped Google.
        tagged, _ = tag_and_sort(results, "default", drop_denied=False)
        return {"query": q, "results": tagged}

    return await rt.run_with_recovery(_do, tool_name="browser.search_google")


@tool(
    "browser.export_state",
    description=(
        "Snapshot the browser's cookies + localStorage to a JSON file. "
        "Useful for backing up a logged-in session before risky operations, "
        "or moving auth to another machine. Read-only."
    ),
    params=ExportStateArgs,
    tags=("browser",),
)
async def browser_export_state(args: ExportStateArgs) -> dict[str, Any]:
    rt = browser()
    # Not side-effectful on the page; take the lock anyway so we don't
    # interleave with a tool that's mid-navigation.
    async with rt.lock():
        target = (
            Path(args.path)
            if args.path
            else paths.data_dir / "browser-state" / f"state-{int(time.time())}.json"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        state = await rt.export_storage_state()
        target.write_text(json.dumps(state, indent=2))
        return {
            "path": str(target),
            "artifacts": [str(target)],
            "cookies": len(state.get("cookies", [])),
            "origins": len(state.get("origins", [])),
        }


def _url_quote(s: str) -> str:
    from urllib.parse import quote_plus

    return quote_plus(s)
