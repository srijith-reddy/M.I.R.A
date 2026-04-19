from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator

from mira.config.settings import get_settings
from mira.obs.logging import log_event
from mira.runtime.llm import Message, llm
from mira.web.chunking import Chunk


@dataclass(frozen=True)
class Synthesis:
    answer: str
    citations: list[dict[str, str]]     # [{"id": "1", "url": "...", "title": "..."}, ...]


_SYSTEM = """\
You are a research synthesis assistant. Answer the user's question using ONLY
the numbered evidence blocks provided. Follow every rule:

1. One or two short sentences. Voice-friendly: no lists, no markdown, no URLs read aloud.
2. Cite the evidence you used with bracketed numbers like [1] or [2][3]. Only cite
   IDs that actually appear in the evidence. Do NOT invent citations.
3. If the evidence does not contain enough information to answer, say exactly:
   "The sources I found don't answer that clearly." Do not guess.
4. Never copy sentence-length strings verbatim — paraphrase tightly.
5. No preamble, no "based on the sources...". Start with the answer.
"""


def _build_evidence_block(chunks: list[Chunk]) -> tuple[str, list[dict[str, str]]]:
    """Flatten chunks into a numbered evidence block and a parallel citation
    list. Chunks are ordered as passed in (already reranked top-k)."""
    lines: list[str] = []
    citations: list[dict[str, str]] = []
    seen_urls: dict[str, str] = {}
    for i, ch in enumerate(chunks, 1):
        cid = str(i)
        citations.append({"id": cid, "url": ch.url, "title": ch.title})
        # Dedupe URLs in the seen map so the caller can emit a compact
        # citation list (one entry per unique source) if it wants to.
        seen_urls.setdefault(ch.url, ch.title)
        snippet = ch.text.strip()
        # Hard cap per chunk to keep the prompt under control — rerank already
        # picked the best ones, so a tail-truncated chunk still carries the
        # relevant signal.
        if len(snippet) > 1200:
            snippet = snippet[:1200] + "..."
        lines.append(f"[{cid}] {snippet}")
    return "\n\n".join(lines), citations


async def synthesize_answer(
    query: str, chunks: list[Chunk], *, model: str | None = None,
) -> Synthesis:
    """Run the synthesis LLM call against the top-k reranked chunks. Returns
    an empty-answer Synthesis if no chunks were provided or the LLM failed —
    callers should fall back to a refusal string in that case."""
    if not chunks:
        return Synthesis(answer="", citations=[])

    settings = get_settings()
    mdl = model or (
        settings.deepseek_planner_model if settings.deepseek_api_key
        else settings.openai_planner_model
    )

    evidence, citations = _build_evidence_block(chunks)
    user_prompt = f"Question: {query}\n\nEvidence:\n{evidence}"

    try:
        resp = await asyncio.to_thread(
            llm().complete,
            [
                Message(role="system", content=_SYSTEM),
                Message(role="user", content=user_prompt),
            ],
            model=mdl,
            temperature=0.2,
            max_tokens=220,
        )
    except Exception as exc:
        log_event("research.synthesize.error", error=repr(exc))
        return Synthesis(answer="", citations=citations)

    answer = (resp.text or "").strip()
    log_event(
        "research.synthesize.ok",
        model=mdl, chars=len(answer), evidence_chunks=len(chunks),
    )
    return Synthesis(answer=answer, citations=citations)


async def synthesize_answer_stream(
    query: str, chunks: list[Chunk], *, model: str | None = None,
) -> tuple[AsyncIterator[str], list[dict[str, str]]]:
    """Streaming version. Yields text deltas as DeepSeek generates them, so
    TTS can start speaking on the first token (~300-500ms) instead of
    waiting for the whole answer (~2-3s). Returns (async_iterator, citations).
    The citations are known up-front from the reranked chunks — only the
    answer text streams."""
    if not chunks:

        async def _empty() -> AsyncIterator[str]:
            if False:
                yield ""
            return

        return _empty(), []

    settings = get_settings()
    mdl = model or (
        settings.deepseek_planner_model if settings.deepseek_api_key
        else settings.openai_planner_model
    )

    evidence, citations = _build_evidence_block(chunks)
    user_prompt = f"Question: {query}\n\nEvidence:\n{evidence}"

    async def _gen() -> AsyncIterator[str]:
        try:
            async for delta in llm().stream(
                [
                    Message(role="system", content=_SYSTEM),
                    Message(role="user", content=user_prompt),
                ],
                model=mdl,
                temperature=0.2,
                max_tokens=220,
            ):
                if delta:
                    yield delta
            log_event(
                "research.synthesize_stream.ok",
                model=mdl, evidence_chunks=len(chunks),
            )
        except Exception as exc:
            log_event("research.synthesize_stream.error", error=repr(exc))

    return _gen(), citations
