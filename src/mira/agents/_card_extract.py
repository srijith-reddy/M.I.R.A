"""Shared post-hoc card extractor for agents that reply in TTS prose.

The browser and communication agents answer the user in one or two
spoken sentences — no bullets, no markdown — so the auto-parser in
`mira.ui.cards` can't synthesize a structured card from their reply.
This module runs a cheap Haiku call in the background that extracts
`{title, subtitle?, trailing?, meta?}` rows from a (transcript, reply)
pair and emits a `ui.card` event directly.

Design notes:
  * **Background, not inline.** Agents call `spawn_card_extractor()` and
    return immediately. TTS starts on the spoken reply while Haiku
    extracts in parallel; the card materializes ~300-500ms after voice
    begins. If we awaited Haiku inline, every carded turn would add
    that latency to time-to-first-audio.
  * **Heuristic gate.** Haiku is cheap but not free. The gate skips
    replies that clearly enumerate one thing (short, no comma-run) and
    replies that already have bullets (the orchestrator's auto-parser
    handles those). Err toward skipping — a missing card is invisible,
    a spurious card is jarring.
  * **Grounded.** Prompt forbids inventing data not in the reply, and
    `temperature=0.0` keeps extraction deterministic. The model is told
    to return `{"rows": []}` when it can't confidently enumerate.
  * **Additive.** Failure or timeout is a no-op. The extractor never
    touches the agent's return value — worst case is no card.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from mira.agents._text import strip_markdown
from mira.obs.logging import log_event
from mira.runtime.llm import Message, llm


_BULLET_LINE = re.compile(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+\S")


_INTENT_KEYWORDS: tuple[str, ...] = (
    "top ", "best ", "list ", "options", "compare", "vs ",
    "which ", "recommend", "show me", "find me",
    # Communication-specific plural intents.
    "reminders", "meetings", "events", "emails", "inbox", "schedule",
    "upcoming", "today", "tomorrow", "this week",
)


def should_extract_card(transcript: str, reply: str) -> bool:
    """Cheap heuristic: does this reply look like it enumerates ≥2 things?
    Conservative by design — a spurious card is worse than a missing one."""
    if not reply or len(reply) < 40:
        return False
    if _BULLET_LINE.search(reply):
        return False  # auto-parser will handle it
    lower_r = reply.lower()
    has_enum_signal = (
        reply.count(",") >= 2
        or "and the " in lower_r
        or re.search(r"\btop\s+\d+|\bbest\s+\d+|\bthree|\bfour|\bfive\b", lower_r) is not None
    )
    if has_enum_signal:
        return True
    lower_t = (transcript or "").lower()
    return any(k in lower_t for k in _INTENT_KEYWORDS)


def spawn_card_extractor(
    *,
    agent: str,
    turn_id: str,
    transcript: str,
    reply: str,
    domain_hint: str | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> None:
    """Kick off background Haiku extraction. No-op if heuristic says skip
    or if there's no running asyncio loop. `domain_hint` is a short phrase
    the prompt uses to bias row-shape extraction toward the agent's
    domain. `sources` is an optional list of `{title, url, thumbnail}` dicts
    collected from web.search during the turn — the extractor asks Haiku
    to pick one source per row so we can attach brand-accurate thumbnails
    in post-processing."""
    if not should_extract_card(transcript, reply):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(
        _extract_task(
            agent=agent,
            turn_id=turn_id,
            transcript=transcript,
            reply=reply,
            domain_hint=domain_hint,
            sources=sources or [],
        )
    )
    task.add_done_callback(_log_task_exception)


async def _extract_task(
    *,
    agent: str,
    turn_id: str,
    transcript: str,
    reply: str,
    domain_hint: str | None,
    sources: list[dict[str, Any]],
) -> None:
    card = await _run_extractor(transcript, reply, domain_hint, sources)
    if not card:
        return
    rows = card.get("rows") or []
    if not rows:
        return
    # Haiku sometimes echoes the reply's **bold** / __emph__ markdown into
    # row fields. The HUD renders plain text, so those asterisks leak
    # through as literal characters. Strip before emission.
    _strip_markdown_card(card, rows)
    if sources:
        _attach_thumbnails(rows, sources)
    # Set `kind` so the Swift HUD picks the right per-domain template.
    # The extractor can't reliably guess this from the reply text; the
    # caller's `agent` name is a far better signal.
    card.setdefault("kind", _kind_for_agent(agent, rows))
    log_event(
        "ui.card",
        **card,
        agent=agent,
        turn_id=turn_id,
    )


def _strip_md(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    return (strip_markdown(text) or "").strip()


def _strip_markdown_card(card: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    for key in ("title", "subtitle", "footer"):
        if key in card:
            card[key] = _strip_md(card[key])
    for row in rows:
        for key in ("title", "subtitle", "trailing", "meta", "badge"):
            if key in row:
                row[key] = _strip_md(row[key])


def _kind_for_agent(agent: str, rows: list[dict[str, Any]]) -> str:
    """Map agent name + row shape to a Swift template kind. Precedence:
    explicit agent domain > row-shape heuristics > generic list."""
    if agent == "commerce":
        return "product"
    if agent == "research":
        return "source"
    if agent == "communication":
        if any(r.get("start_time") for r in rows):
            return "calendar"
        return "email"
    if agent in ("browser", "device"):
        return "action"
    return "list"


def _attach_thumbnails(
    rows: list[dict[str, Any]], sources: list[dict[str, Any]]
) -> None:
    """Match each row's `source_url` (picked by Haiku) to a source and
    copy its thumbnail onto the row. Also stamp the row's `url` so
    clicking opens the source. Silent no-op for rows Haiku couldn't
    confidently ground — better a missing image than a wrong one."""
    by_url = {s.get("url"): s for s in sources if s.get("url")}
    for row in rows:
        src_url = row.pop("source_url", None)
        if not src_url:
            continue
        match = by_url.get(src_url)
        if match is None:
            continue
        thumb = match.get("thumbnail")
        if thumb and not row.get("thumbnail"):
            row["thumbnail"] = thumb
        if not row.get("url"):
            row["url"] = src_url


async def _run_extractor(
    transcript: str, reply: str, domain_hint: str | None,
    sources: list[dict[str, Any]],
) -> dict[str, Any] | None:
    domain_line = (
        f"Domain context: this reply is from the {domain_hint} agent.\n"
        if domain_hint
        else ""
    )
    # When we have source URLs from web.search during the turn, ask the
    # model to pick the best-matching one per row. We use that URL post-hoc
    # to attach the thumbnail. Keep the source list tight — Haiku's context
    # is paid for per token and 10 sources is enough coverage.
    if sources:
        source_lines = []
        for i, s in enumerate(sources[:10]):
            title = (s.get("title") or "")[:80]
            url = s.get("url") or ""
            source_lines.append(f'  {i+1}. "{title}" — {url}')
        sources_block = (
            "Web sources gathered for this turn (pick the single best "
            "`source_url` for each row from this list, or omit if none "
            "clearly matches):\n" + "\n".join(source_lines) + "\n\n"
        )
        source_rule = (
            " Include a `source_url` field per row set to the exact URL "
            "from the sources list that best matches that row. If no source "
            "clearly matches a row, omit `source_url` for it."
        )
    else:
        sources_block = ""
        source_rule = ""
    system = (
        "You extract a compact visual card from a voice assistant's "
        "spoken reply. Return JSON with fields: card_type='list', "
        "title (short, ≤60 chars), rows (array of {title, subtitle?, "
        "trailing?, meta?, source_url?}). 'trailing' is for right-aligned "
        "values like prices, ratings, times, or dates. Return {\"rows\": []} "
        "if the reply enumerates only one thing or the structure is "
        "unclear. Never invent data not in the reply. Keep row titles "
        "≤40 chars. Output plain text only — no markdown, no "
        "**bold**, no __emphasis__, no backticks. Output JSON only."
        + source_rule
    )
    user = (
        f"{domain_line}"
        f"{sources_block}"
        f"User asked: {transcript.strip()}\n\n"
        f"Assistant reply: {reply.strip()}\n\n"
        "Extract the card."
    )
    try:
        def _call() -> Any:
            return llm().complete(
                [
                    Message(role="system", content=system),
                    Message(role="user", content=user),
                ],
                model="claude-haiku-4-5",
                temperature=0.0,
                max_tokens=400,
                response_format={"type": "json_object"},
            )
        resp = await asyncio.wait_for(asyncio.to_thread(_call), timeout=3.0)
    except Exception as exc:
        log_event("card_extract.error", error=repr(exc))
        return None
    raw = (resp.text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL)
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    parsed.setdefault("card_type", "list")
    parsed.setdefault("title", transcript.strip()[:60] or "Results")
    return parsed


def _log_task_exception(task: asyncio.Task) -> None:
    try:
        exc = task.exception()
    except (asyncio.CancelledError, asyncio.InvalidStateError):
        return
    if exc is not None:
        log_event("card_extract.task_error", error=repr(exc))
