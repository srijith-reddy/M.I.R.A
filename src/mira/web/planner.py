from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from mira.config.settings import get_settings
from mira.obs.logging import log_event
from mira.runtime.llm import Message, llm

TrustMode = Literal[
    "off", "default", "strict", "news", "commerce", "booking", "reference"
]

_TRUST_MODES = {"off", "default", "strict", "news", "commerce", "booking", "reference"}


@dataclass(frozen=True)
class PlanResult:
    queries: list[str]
    trust_mode: TrustMode


_SYSTEM = """\
You rewrite and decompose a user's research question into focused web-search
queries.

Rules:
- Return JSON: {"queries": [string, ...], "trust_mode": string}
- 1-3 queries. More = more latency, not always more signal.
- Rewrite into search-engine idiom: concrete nouns, year if it helps,
  no first-person, no filler. Decompose only when the question truly has
  multiple sub-questions.
- Pick trust_mode from: default, news, commerce, booking, reference, strict.
  - news: current events, sports scores, breaking news, "today"/"tonight"
  - commerce: shopping, prices, product comparisons
  - booking: travel, flights, hotels, tickets
  - reference: docs, how-to, academic, language references
  - strict: medical, financial, legal — domain gating matters
  - default: everything else
- Output JSON only. No commentary, no markdown fences.
"""


async def plan_query(user_query: str) -> PlanResult:
    """Ask the planner LLM to rewrite + decompose the query and pick a trust
    mode. Returns a sane fallback (single query, default mode) if the model
    is unavailable or produces unparseable output — this path must never
    block research.deep from running."""
    settings = get_settings()
    model = settings.deepseek_planner_model if settings.deepseek_api_key else None

    user_query = user_query.strip()
    if not user_query:
        return PlanResult(queries=[], trust_mode="default")

    if model is None:
        # No planner credential wired — skip the call rather than pay the
        # OpenAI rate. The pipeline still works with the raw query.
        return PlanResult(queries=[user_query], trust_mode="default")

    try:
        resp = await _acomplete(
            model=model,
            messages=[
                Message(role="system", content=_SYSTEM),
                Message(role="user", content=user_query),
            ],
        )
        text = (resp.text or "").strip()
        data = _extract_json(text)
        queries = [q.strip() for q in (data.get("queries") or []) if isinstance(q, str) and q.strip()]
        mode_raw = (data.get("trust_mode") or "default").strip().lower()
        mode: TrustMode = mode_raw if mode_raw in _TRUST_MODES else "default"  # type: ignore[assignment]
        if not queries:
            queries = [user_query]
        # Cap at 3 — more than that is usually the model padding output.
        queries = queries[:3]
        log_event(
            "research.planner.ok",
            original=user_query, queries=queries, trust_mode=mode,
        )
        return PlanResult(queries=queries, trust_mode=mode)
    except Exception as exc:
        log_event("research.planner.error", error=repr(exc))
        return PlanResult(queries=[user_query], trust_mode="default")


async def _acomplete(*, model: str, messages: list[Message]):
    """Thin async wrapper so the planner doesn't block the event loop on the
    sync OpenAI SDK call. Runs the (SDK-internally-sync) completion in a
    thread — the planner call is short enough that the thread hop is free
    compared to the network round-trip."""
    import asyncio

    return await asyncio.to_thread(
        llm().complete,
        messages,
        model=model,
        temperature=0.1,
        max_tokens=200,
        response_format={"type": "json_object"},
    )


def _extract_json(text: str) -> dict:
    """Be forgiving — models sometimes wrap JSON in ``` fences or prose
    despite being asked for raw JSON. Strip common wrappers before parsing."""
    text = text.strip()
    if text.startswith("```"):
        # ```json\n{...}\n```
        text = text.strip("`")
        nl = text.find("\n")
        if nl > 0 and text[:nl].lower().startswith("json"):
            text = text[nl + 1:]
        text = text.strip("` \n")
    try:
        obj = json.loads(text)
    except Exception:
        # Last resort — grab the biggest {...} block.
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            obj = json.loads(text[start:end + 1])
        else:
            raise
    if not isinstance(obj, dict):
        raise ValueError("planner JSON was not an object")
    return obj
