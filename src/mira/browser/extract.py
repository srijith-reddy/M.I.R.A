from __future__ import annotations

from typing import Any

# Priority order: most-signal containers first, fall back to body.
_PRIORITY_SELECTORS = ("main", "article", "[role='main']", "body")


def _truncate_on_boundary(text: str, max_chars: int) -> str:
    """Cap `text` at `max_chars`, preferring a paragraph → sentence → word
    boundary over a mid-token cut.

    Why not just slice: the LLM interprets the tail of a truncated string as
    real content. A cut mid-sentence can read as a claim ("the score was 126
    to"). A paragraph boundary is much easier to reason about."""
    if len(text) <= max_chars:
        return text

    window = text[:max_chars]

    # Paragraph break — prefer the last \n\n in the last 20% of the window.
    search_from = int(max_chars * 0.8)
    para = window.rfind("\n\n", search_from)
    if para != -1:
        return window[:para].rstrip() + "\n\n…"

    # Single newline as a weaker paragraph signal.
    nl = window.rfind("\n", search_from)
    if nl != -1:
        return window[:nl].rstrip() + "\n…"

    # Sentence end.
    for punct in (". ", "! ", "? "):
        idx = window.rfind(punct, search_from)
        if idx != -1:
            return window[: idx + 1] + " …"

    # Word boundary — last resort before a hard cut.
    space = window.rfind(" ", search_from)
    if space != -1:
        return window[:space].rstrip() + " …"

    return window + "…"


async def extract_clean_text(
    page: Any,
    *,
    selector: str | None = None,
    max_chars: int = 8000,
) -> str:
    """Read meaningful text from the current page.

    Heuristic: try `<main>` → `<article>` → `[role=main]` → `<body>`, stopping
    at the first element that exists and has content. We rely on Playwright's
    `inner_text()` (not `text_content()`) so the browser's own layout engine
    handles whitespace/hidden-element collapsing, which is closer to what a
    user actually reads.

    Hard cap on output size so a single tool call can't blow the LLM context.
    Truncation snaps to a paragraph / sentence / word boundary so the LLM
    doesn't see a half-finished claim at the tail.
    """
    if selector:
        try:
            text = await page.locator(selector).first.inner_text(timeout=3000)
        except Exception:
            text = ""
        return _truncate_on_boundary((text or "").strip(), max_chars)

    for sel in _PRIORITY_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            text = await loc.inner_text(timeout=3000)
        except Exception:
            continue
        text = (text or "").strip()
        if text:
            return _truncate_on_boundary(text, max_chars)
    return ""
