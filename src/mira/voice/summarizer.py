"""Pre-TTS compression pass.

Cartesia bills by character. Replies over ~30 words are usually verbose
rephrasings of a shorter answer, so we route them through Haiku with a
tight "compress this" prompt. A ~$0.0001 Haiku call saves ~$0.003 of
Cartesia on anything > 40 words — net positive past that threshold, and
the user perceives a tighter reply either way.

Kept deliberately minimal: one public async function, no classes, no
config. If it errors or times out, we return the original text — TTS
must never block on this step."""

from __future__ import annotations

import asyncio
import time

from mira.obs.logging import log_event
from mira.runtime.llm import Message, llm


# Threshold chosen by noise-floor: anything under ~30 words is usually
# already as short as it gets, and compressing a 20-word reply to 15
# words isn't worth the 300ms Haiku RTT.
MIN_WORDS_TO_COMPRESS = 30
TARGET_WORDS = 20
MODEL = "claude-haiku-4-5"
TIMEOUT_S = 2.0


_SYSTEM = (
    "You compress a voice assistant's spoken reply to be shorter and "
    "more natural for TTS. Rules: "
    "(1) keep the first sentence's meaning; "
    f"(2) output ≤ {TARGET_WORDS} words; "
    "(3) no markdown, no lists, no quotes, no stage directions; "
    "(4) preserve any specific numbers, names, times, or prices verbatim; "
    "(5) output ONLY the compressed reply — no preface, no explanation."
)


def _word_count(text: str) -> int:
    return len(text.split())


async def compress(text: str) -> str:
    """Return a tightened version of `text`, or `text` unchanged if it's
    already short enough, if Haiku errors, or if the timeout trips."""
    if not text or not text.strip():
        return text
    wc = _word_count(text)
    if wc < MIN_WORDS_TO_COMPRESS:
        return text

    t0 = time.perf_counter()
    try:
        resp = await asyncio.wait_for(
            asyncio.to_thread(
                llm().complete,
                [
                    Message(role="system", content=_SYSTEM),
                    Message(role="user", content=text),
                ],
                model=MODEL,
                temperature=0.2,
                max_tokens=120,
            ),
            timeout=TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        log_event("summarizer.timeout", original_words=wc, timeout_s=TIMEOUT_S)
        return text
    except Exception as exc:
        log_event("summarizer.error", original_words=wc, error=repr(exc))
        return text

    compressed = (resp.text or "").strip().strip('"').strip("'")
    if not compressed:
        return text
    new_wc = _word_count(compressed)
    # Reject anything the model didn't actually shorten (rare, but costs
    # us money for no benefit).
    if new_wc >= wc:
        log_event(
            "summarizer.no_op",
            original_words=wc,
            returned_words=new_wc,
            latency_ms=round((time.perf_counter() - t0) * 1000, 1),
        )
        return text
    log_event(
        "summarizer.compressed",
        original_words=wc,
        compressed_words=new_wc,
        latency_ms=round((time.perf_counter() - t0) * 1000, 1),
        cost_usd=round(resp.usage.cost_usd, 6),
    )
    return compressed
