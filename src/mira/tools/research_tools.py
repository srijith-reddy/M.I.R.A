from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from mira.obs.logging import log_event
from mira.runtime.registry import registry, tool
from mira.runtime.schemas import ToolCall
from mira.web.chunking import chunk_document
from mira.web.planner import plan_query
from mira.web.rerank import hybrid_rerank
from mira.web.retrieval import progressive_fetch
from mira.web.synthesize import synthesize_answer


class ResearchArgs(BaseModel):
    query: str = Field(..., description="What to research.")
    max_sources: int = Field(
        default=5, ge=1, le=8,
        description="How many of the top search results to read in full.",
    )
    top_chunks: int = Field(
        default=5, ge=1, le=15,
        description="How many reranked chunks to return to the caller.",
    )
    synthesize: bool = Field(
        default=True,
        description=(
            "Run an LLM synthesis pass over the reranked chunks so the caller "
            "gets a single-sentence answer + citations. Turn off for callers "
            "that want raw evidence only."
        ),
    )


def _summarize_research(data: Any) -> str:
    if not isinstance(data, dict):
        return str(data)
    if not data.get("ok"):
        return f"research error: {data.get('error') or 'unknown'}"
    answer = (data.get("answer") or "").strip()
    citations = data.get("citations") or []
    sources = data.get("sources") or []
    if answer:
        # Compact citation tail — dedupe by URL so a multi-chunk single source
        # doesn't produce [1][2][3] all pointing to the same article.
        seen: set[str] = set()
        cite_lines: list[str] = []
        for c in citations:
            url = c.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            title = (c.get("title") or "").strip() or url
            cite_lines.append(f"[{c.get('id')}] {title} — {url}")
        if cite_lines:
            return answer + "\n\nsources:\n" + "\n".join(cite_lines)
        return answer
    # No synthesis — render the chunk evidence directly.
    chunks = data.get("chunks") or []
    if not chunks:
        return f"no evidence found for '{data.get('query', '')}'"
    lines: list[str] = [f"evidence for '{data['query']}':"]
    for i, ch in enumerate(chunks, 1):
        title = (ch.get("title") or "").strip() or ch.get("url", "")
        url = ch.get("url", "")
        preview = (ch.get("text") or "").replace("\n", " ").strip()
        if len(preview) > 400:
            preview = preview[:400] + "..."
        lines.append(f"[{i}] {title} — {url}\n    {preview}")
    if sources:
        lines.append(f"(read {len(sources)} source{'s' if len(sources) != 1 else ''})")
    return "\n".join(lines)


async def _run_search(
    query: str, max_results: int, *, trust_mode: str = "default",
) -> list[dict[str, Any]]:
    """Dispatch web.search through the registry so the brave-key gating and
    in-memory result cache are both respected. Returns the result list or
    [] when search isn't available / returned nothing."""
    spec = registry().get("web.search")
    if spec is None:
        return []
    res = await registry().dispatch(ToolCall(
        call_id="research.search",
        tool="web.search",
        args={
            "query": query, "max_results": max_results,
            "trust_mode": trust_mode,
        },
    ))
    if not res.ok or not isinstance(res.data, dict):
        return []
    return [r for r in (res.data.get("results") or []) if r.get("url")]


async def _multi_search(
    queries: list[str], per_query: int, trust_mode: str,
) -> list[dict[str, Any]]:
    """Run every planned query in parallel, then RRF-merge the result lists
    by URL. A URL that appears high in multiple queries ranks higher than
    one that only appears in a single long list, which is exactly the
    signal we want for "most relevant across angles of the question"."""
    if not queries:
        return []
    task_results = await asyncio.gather(*[
        _run_search(q, per_query, trust_mode=trust_mode) for q in queries
    ])
    # RRF merge keyed on URL.
    k = 60
    scores: dict[str, float] = {}
    payload: dict[str, dict[str, Any]] = {}
    for results in task_results:
        for pos, r in enumerate(results):
            url = r.get("url")
            if not url:
                continue
            scores[url] = scores.get(url, 0.0) + 1.0 / (k + pos)
            # Keep the earliest-seen payload — it usually has the richest
            # snippet because top positions get more metadata from Brave.
            payload.setdefault(url, r)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [payload[url] for url, _ in ranked]


@tool(
    "research.deep",
    description=(
        "Deep web research: plans subqueries, searches them in parallel, reads "
        "top sources in parallel with a JavaScript-rendering fallback for "
        "SPA/anti-bot pages, chunks and hybrid-reranks the content, then "
        "synthesizes a one-sentence answer with citations. Use this for "
        "factual questions that need evidence — current events, specific "
        "claims, 'what is the latest on X'. Returns cited answer plus the "
        "evidence chunks that supported it."
    ),
    params=ResearchArgs,
    tags=("web", "research"),
    summarizer=_summarize_research,
    volatile=True,
)
async def research_deep(args: ResearchArgs) -> dict[str, Any]:
    plan = await plan_query(args.query)
    log_event(
        "research.deep.start",
        query=args.query, planned=plan.queries, trust_mode=plan.trust_mode,
    )

    # Ask for `max_sources` per query but cap merged URLs at `max_sources`
    # after dedupe — more queries widen the candidate pool without blowing
    # up the fetch budget.
    merged = await _multi_search(plan.queries, args.max_sources, plan.trust_mode)
    urls = [r["url"] for r in merged[: args.max_sources]]
    if not urls:
        return {
            "ok": False,
            "error": "no search results (is BRAVE_SEARCH_API_KEY set?)",
            "query": args.query,
            "planned_queries": plan.queries,
        }

    outcomes = await progressive_fetch(urls)

    chunks = []
    sources = []
    for o in outcomes:
        if not o.text:
            continue
        doc_chunks = chunk_document(
            o.text, url=o.url, title=o.title, source_rank=o.source_rank,
        )
        chunks.extend(doc_chunks)
        sources.append({
            "url": o.url, "title": o.title,
            "via": o.via, "source_rank": o.source_rank,
            "chunks": len(doc_chunks),
        })

    if not chunks:
        return {
            "ok": False,
            "error": "fetched sources but extraction produced no content",
            "query": args.query,
            "planned_queries": plan.queries,
            "sources": sources,
        }

    top = hybrid_rerank(args.query, chunks, top_k=args.top_chunks)
    log_event(
        "research.deep.reranked",
        query=args.query, sources=len(sources),
        total_chunks=len(chunks), returned=len(top),
    )

    result: dict[str, Any] = {
        "ok": True,
        "query": args.query,
        "planned_queries": plan.queries,
        "trust_mode": plan.trust_mode,
        "chunks": [
            {
                "text": c.text, "url": c.url, "title": c.title,
                "source_rank": c.source_rank, "chunk_idx": c.chunk_idx,
            }
            for c in top
        ],
        "sources": sources,
    }

    if args.synthesize:
        synth = await synthesize_answer(args.query, top)
        result["answer"] = synth.answer
        result["citations"] = synth.citations

    return result
