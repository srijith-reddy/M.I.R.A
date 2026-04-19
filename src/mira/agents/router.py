from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from mira.config.settings import get_settings
from mira.obs.logging import log_event
from mira.runtime.llm import Message, llm
from mira.runtime.schemas import RouterDecision, RouterDecisionKind
from mira.runtime.tracing import span

# Regex fast-path for pure smalltalk. These utterances never need a tool
# chain or specialist; sending them through the router LLM burns ~80ms and
# ~90 tokens for no benefit. Match on the normalized transcript (lowercase,
# punctuation stripped) so "Thanks!" and "  THANKS " both hit.
_SMALLTALK_PATTERNS = [
    re.compile(r"^(hi|hey|hello|yo|sup|howdy)( mira)?$"),
    re.compile(r"^(thanks|thank you|thx|ty|cheers|appreciate it)$"),
    re.compile(r"^(ok|okay|cool|got it|nice|great|sounds good|alright)$"),
    re.compile(r"^(bye|goodbye|cya|later|see (ya|you))$"),
    re.compile(r"^(stop|cancel|never ?mind|forget it)$"),
    re.compile(r"^(good (morning|afternoon|evening|night))( mira)?$"),
    re.compile(r"^(yes|yeah|yep|yup|no|nope)$"),
]

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    s = text.strip().lower()
    s = _PUNCT.sub("", s)
    s = _WS.sub(" ", s).strip()
    return s


# Confidence floor under which a "direct" route is demoted to "supervisor".
# A router that's unsure which specialist to use burns more time with a
# wrong direct dispatch than with one supervisor hop.
_MIN_DIRECT_CONFIDENCE = 0.3

# Router-decision cache. Keyed on the normalized transcript — identical
# utterances within the window skip the router LLM call entirely. 5min TTL
# is long enough to capture tight repeats ("play music" twice in a session)
# but short enough that a changed agent catalog (agent added/removed) gets
# reflected quickly on the next cold call. Low-confidence "supervisor"
# decisions aren't cached because the planner's downstream behavior may
# legitimately vary turn-to-turn; only confident routes cache.
_DECISION_CACHE_TTL_S = 300.0
_DECISION_CACHE_MAX = 128
_decision_cache: dict[str, tuple[float, RouterDecision]] = {}


def _cache_get(key: str) -> RouterDecision | None:
    if not key:
        return None
    hit = _decision_cache.get(key)
    if hit is None:
        return None
    expires_at, decision = hit
    if expires_at < time.time():
        _decision_cache.pop(key, None)
        return None
    return decision


def _cache_put(key: str, decision: RouterDecision) -> None:
    if not key:
        return
    # Only cache confident decisions. A low-confidence supervisor route
    # means the router wasn't sure; caching it would lock in uncertainty.
    if decision.confidence < 0.6:
        return
    _decision_cache[key] = (time.time() + _DECISION_CACHE_TTL_S, decision)
    if len(_decision_cache) > _DECISION_CACHE_MAX:
        now = time.time()
        for k, (exp, _) in list(_decision_cache.items()):
            if exp < now:
                _decision_cache.pop(k, None)
        if len(_decision_cache) > _DECISION_CACHE_MAX:
            oldest = sorted(_decision_cache.items(), key=lambda kv: kv[1][0])
            for k, _ in oldest[: len(_decision_cache) - _DECISION_CACHE_MAX]:
                _decision_cache.pop(k, None)

_SYSTEM_PROMPT = """\
You are MIRA's Tier-0 router. Your only job is to decide, as fast as
possible, where a user's single-turn utterance should go. You do not answer
the user. You produce strictly one JSON object matching this schema:

{
  "kind": "direct" | "supervisor" | "smalltalk",
  "agent": string | null,
  "confidence": number in [0, 1],
  "reason": short string
}

Rules:
- "direct": obvious single-specialist request (research Q&A, web lookup,
  simple reminder). Set "agent" to the specialist name.
- "supervisor": multi-step task, ambiguous intent, anything requiring
  coordination, confirmation, or a tool chain. "agent" must be null.
- "smalltalk": pure chit-chat, greetings, closers — no tools, no state.
  "agent" must be null.

Be biased toward "supervisor" when unsure. Wrong "direct" routes cost more
than a single extra hop. Output JSON only. No prose.
"""


def _fallback_decision(reason: str) -> RouterDecision:
    return RouterDecision(
        kind="supervisor", agent=None, confidence=0.0, reason=reason
    )


class FastRouter:
    """Tier-0 routing decision. One cheap LLM call with strict JSON output.

    Why this exists: ~60–70% of turns are either smalltalk or a single
    specialist's job. Sending those through the full Supervisor plan cycle
    burns ~500–800ms and a lot of tokens for no benefit. The router runs
    on the cheapest available model and returns a structured decision the
    orchestrator can act on immediately."""

    def __init__(self) -> None:
        self._settings = get_settings()

    def _router_model(self) -> str:
        """Pick the cheapest available model for routing.

        Groq-hosted Llama is roughly 10x cheaper and 2-3x faster than the
        OpenAI classify model for this workload (~60 tokens in, ~30 out,
        strict JSON). Fall back to the OpenAI classify model when no Groq
        key is configured — every machine always has the OpenAI key for
        embeddings, so this fallback is reliable."""
        if self._settings.groq_api_key:
            return self._settings.groq_router_model
        return self._settings.openai_classify_model

    async def decide(
        self,
        transcript: str,
        *,
        agents_catalog: list[dict[str, Any]],
        prior_turn: dict[str, Any] | None = None,
    ) -> RouterDecision:
        model = self._router_model()
        with span("router.decide", n_agents=len(agents_catalog), model=model):
            if not transcript.strip():
                return _fallback_decision("empty transcript")

            norm = _normalize(transcript)
            if norm and any(p.match(norm) for p in _SMALLTALK_PATTERNS):
                log_event("router.regex_smalltalk", transcript=transcript[:80])
                return RouterDecision(
                    kind="smalltalk",
                    agent=None,
                    confidence=1.0,
                    reason="regex smalltalk",
                )

            # Short utterances are highly ambiguous without context — "14
            # inch M5 Pro" could be a spec clarification continuing a
            # shopping thread or a standalone device query. Bypass the
            # decision cache so the prior turn steers routing each time.
            is_short_followup = len(norm) < 30 and prior_turn is not None
            if not is_short_followup:
                cached = _cache_get(norm)
                if cached is not None:
                    log_event(
                        "router.cache_hit", kind=cached.kind, agent=cached.agent
                    )
                    return cached

            catalog_lines = "\n".join(
                f"- {a['name']}: {a.get('purpose', '')}" for a in agents_catalog
            )
            system = _SYSTEM_PROMPT + "\n\nAvailable specialists:\n" + catalog_lines
            if prior_turn is not None:
                prior_q = (prior_turn.get("transcript") or "")[:200]
                prior_a = (prior_turn.get("reply") or "")[:400]
                prior_via = prior_turn.get("via") or ""
                system += (
                    "\n\nPrevious turn (for continuity):\n"
                    f"  user: {prior_q}\n"
                    f"  assistant: {prior_a}\n"
                    f"  routed via: {prior_via}\n"
                    "If the current utterance is a short fragment, clarification, "
                    "or spec that extends the previous exchange (e.g. answering "
                    "a question the assistant just asked), route it to the same "
                    "domain as the previous turn. A standalone fresh topic still "
                    "routes on its own merits."
                )
            messages = [
                Message(role="system", content=system),
                Message(role="user", content=transcript),
            ]

            def _call() -> str:
                resp = llm().complete(
                    messages,
                    model=model,
                    temperature=0.0,
                    max_tokens=120,
                    response_format={"type": "json_object"},
                )
                return resp.text

            try:
                raw = await asyncio.to_thread(_call)
            except Exception as exc:
                log_event("router.llm_error", error=repr(exc))
                return _fallback_decision(f"llm error: {exc}")

            try:
                parsed = json.loads(raw)
                kind_val = parsed.get("kind", "supervisor")
                if kind_val not in ("direct", "supervisor", "smalltalk"):
                    kind_val = "supervisor"
                kind: RouterDecisionKind = kind_val  # type: ignore[assignment]
                agent_name = parsed.get("agent")
                if kind != "direct":
                    agent_name = None
                if kind == "direct" and agent_name not in {
                    a["name"] for a in agents_catalog
                }:
                    # Router hallucinated an agent name — safe fallback.
                    return _fallback_decision(
                        f"unknown direct target: {agent_name}"
                    )
                confidence = float(parsed.get("confidence", 0.0))
                # Low-confidence direct routes are worse than a supervisor
                # hop: a wrong specialist dispatches tools it shouldn't.
                # Demote to supervisor and let the planner sort it out.
                if kind == "direct" and confidence < _MIN_DIRECT_CONFIDENCE:
                    log_event(
                        "router.low_confidence_demoted",
                        confidence=confidence,
                        agent=agent_name,
                    )
                    kind = "supervisor"
                    agent_name = None
                decision = RouterDecision(
                    kind=kind,
                    agent=agent_name,
                    confidence=confidence,
                    reason=str(parsed.get("reason", ""))[:200],
                )
                # Don't cache context-dependent decisions — the same short
                # utterance should be free to re-route if the conversation
                # has moved on.
                if not is_short_followup:
                    _cache_put(norm, decision)
                return decision
            except Exception as exc:
                log_event("router.parse_error", error=repr(exc), raw=raw[:200])
                return _fallback_decision("parse error")


_router: FastRouter | None = None


def router() -> FastRouter:
    global _router
    if _router is None:
        _router = FastRouter()
    return _router
