from __future__ import annotations

from typing import Any, AsyncIterator

import time

from mira.config.settings import get_settings
from mira.obs.logging import log_event
from mira.runtime.llm_types import LLMResponse, LLMUsage, Message, Provider, Role
from mira.runtime.providers import (
    _AnthropicAdapter,
    _OpenAICompatAdapter,
    provider_for,
)
from mira.runtime.tracing import span

# Re-exported so existing imports (`from mira.runtime.llm import Message,
# LLMResponse, ...`) keep working. Types live in llm_types.py to avoid a
# cycle with providers.py.
__all__ = [
    "LLMGateway",
    "LLMResponse",
    "LLMUsage",
    "Message",
    "Provider",
    "Role",
    "llm",
]


# Per-1K-token prices (USD). Coarse table; update when providers change pricing.
# Keep conservative — used for budget estimation, not billing. Lives in
# llm.py rather than providers.py because estimation is a gateway concern
# (we compute cost after the provider returns raw token counts).
_COST_TABLE: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o": (0.005, 0.015),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4.1": (0.002, 0.008),
    "gpt-4.1-mini": (0.0004, 0.0016),
    # Anthropic
    "claude-opus-4-7": (0.015, 0.075),
    "claude-sonnet-4-6": (0.003, 0.015),
    "claude-haiku-4-5": (0.001, 0.005),
    # Groq (cheap tier-0 router models)
    "llama-3.1-8b-instant": (0.00005, 0.00008),
    "llama-3.3-70b-versatile": (0.00059, 0.00079),
    # DeepSeek — V3 (`deepseek-chat`) is a strong planner/supervisor at
    # ~1/20th of gpt-4o pricing. R1 (`deepseek-reasoner`) trades latency
    # for reasoning quality.
    "deepseek-chat": (0.00027, 0.0011),
    "deepseek-reasoner": (0.00055, 0.00219),
}


# Provider-published cache discounts, expressed as a multiplier on normal
# input rate:
#   OpenAI prompt caching  → reads at 0.5x input                        (auto, >1024 tok)
#   DeepSeek prefix cache  → reads at ~0.25x input ($0.07 vs $0.27 /1M) (auto)
#   Anthropic ephemeral    → reads at 0.10x input, writes at 1.25x      (opt-in)
# If a model isn't listed, treat cached tokens as normal input — safer to
# overestimate than to under-bill a user.
_CACHE_READ_MULT: dict[str, float] = {
    "gpt-4o": 0.5,
    "gpt-4o-mini": 0.5,
    "gpt-4.1": 0.25,
    "gpt-4.1-mini": 0.25,
    "deepseek-chat": 0.25,
    "deepseek-reasoner": 0.25,
    "claude-opus-4-7": 0.1,
    "claude-sonnet-4-6": 0.1,
    "claude-haiku-4-5": 0.1,
}
_CACHE_WRITE_MULT: dict[str, float] = {
    # Only Anthropic charges for cache writes; OpenAI/DeepSeek write for free.
    "claude-opus-4-7": 1.25,
    "claude-sonnet-4-6": 1.25,
    "claude-haiku-4-5": 1.25,
}


def _estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    cached_prompt_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    pin, pout = _COST_TABLE.get(model, (0.0, 0.0))
    read_mult = _CACHE_READ_MULT.get(model, 1.0)
    write_mult = _CACHE_WRITE_MULT.get(model, 1.0)
    # Uncached input = total prompt - cache reads - cache writes. Writes are
    # billed separately at write_mult; reads at read_mult.
    fresh = max(0, prompt_tokens - cached_prompt_tokens - cache_creation_tokens)
    cost = (fresh / 1000) * pin
    cost += (cached_prompt_tokens / 1000) * pin * read_mult
    cost += (cache_creation_tokens / 1000) * pin * write_mult
    cost += (completion_tokens / 1000) * pout
    return cost


class LLMGateway:
    """Unified LLM entrypoint. Every agent goes through this — never directly to a vendor SDK.

    Responsibilities:
      * Lazy adapter init per provider (no crash at import when keys are missing).
      * Dispatch by model name → provider via `providers.provider_for`.
      * Trace every call via `runtime.tracing.span`.
      * Populate token usage and estimated cost from the coarse `_COST_TABLE`.
      * Sync `complete()` + async `stream()` — streaming is the default path in
        the hot turn so TTS can start on the first token.

    Adapters (OpenAI / Anthropic / Groq) are held lazily so the gateway can be
    constructed on a machine that only has one key set.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._openai: _OpenAICompatAdapter | None = None
        self._groq: _OpenAICompatAdapter | None = None
        self._deepseek: _OpenAICompatAdapter | None = None
        self._anthropic: _AnthropicAdapter | None = None

    # ---- Adapter accessors -------------------------------------------------

    def _openai_adapter(self) -> _OpenAICompatAdapter:
        if self._openai is None:
            key = self._settings.openai_api_key
            if not key:
                raise RuntimeError(
                    "OPENAI_API_KEY is not set; cannot route to an OpenAI model."
                )
            self._openai = _OpenAICompatAdapter(provider="openai", api_key=key)
        return self._openai

    def _groq_adapter(self) -> _OpenAICompatAdapter:
        if self._groq is None:
            key = self._settings.groq_api_key
            if not key:
                raise RuntimeError(
                    "GROQ_API_KEY is not set; cannot route to a Groq model."
                )
            self._groq = _OpenAICompatAdapter(
                provider="groq",
                api_key=key,
                base_url="https://api.groq.com/openai/v1",
            )
        return self._groq

    def _deepseek_adapter(self) -> _OpenAICompatAdapter:
        if self._deepseek is None:
            key = self._settings.deepseek_api_key
            if not key:
                raise RuntimeError(
                    "DEEPSEEK_API_KEY is not set; cannot route to a DeepSeek model."
                )
            self._deepseek = _OpenAICompatAdapter(
                provider="deepseek",
                api_key=key,
                base_url="https://api.deepseek.com/v1",
            )
        return self._deepseek

    def _anthropic_adapter(self) -> _AnthropicAdapter:
        if self._anthropic is None:
            key = self._settings.anthropic_api_key
            if not key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is not set; cannot route to a Claude model."
                )
            self._anthropic = _AnthropicAdapter(api_key=key)
        return self._anthropic

    def _adapter_for(self, model: str) -> Any:
        prov: Provider = provider_for(model)
        if prov == "openai":
            return self._openai_adapter()
        if prov == "groq":
            return self._groq_adapter()
        if prov == "deepseek":
            return self._deepseek_adapter()
        if prov == "anthropic":
            return self._anthropic_adapter()
        # Unreachable given the Provider Literal, but keep the fallback
        # explicit rather than relying on an implicit None.
        raise RuntimeError(f"no adapter for provider: {prov}")

    # ---- Public surface ----------------------------------------------------

    def complete(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 800,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Single-shot completion. Returns a typed `LLMResponse` and emits a traced span."""
        mdl = model or self._settings.openai_planner_model
        adapter = self._adapter_for(mdl)
        with span(
            "llm.complete",
            model=mdl,
            provider=adapter.provider,
            n_messages=len(messages),
            has_tools=bool(tools),
            structured=bool(response_format),
        ):
            t0 = time.perf_counter()
            resp = adapter.complete(
                model=mdl,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                response_format=response_format,
            )
            # Adapters return usage with cost=0; fill it in here so the cost
            # table is the gateway's single source of truth.
            if resp.usage.cost_usd == 0.0 and (
                resp.usage.prompt_tokens or resp.usage.completion_tokens
            ):
                resp.usage.cost_usd = _estimate_cost(
                    mdl,
                    resp.usage.prompt_tokens,
                    resp.usage.completion_tokens,
                    cached_prompt_tokens=resp.usage.cached_prompt_tokens,
                    cache_creation_tokens=resp.usage.cache_creation_tokens,
                )
            # Emit a per-call event so the dashboard can roll up cost + token
            # usage per turn. The `turn_id` auto-enrichment in `log_event`
            # will attach the current turn context — no need to plumb it.
            cache_hit_rate = (
                round(resp.usage.cached_prompt_tokens / resp.usage.prompt_tokens, 3)
                if resp.usage.prompt_tokens
                else 0.0
            )
            log_event(
                "llm.call",
                model=mdl,
                provider=adapter.provider,
                prompt_tokens=resp.usage.prompt_tokens,
                completion_tokens=resp.usage.completion_tokens,
                cached_prompt_tokens=resp.usage.cached_prompt_tokens,
                cache_creation_tokens=resp.usage.cache_creation_tokens,
                cache_hit_rate=cache_hit_rate,
                cost_usd=resp.usage.cost_usd,
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
                finish_reason=resp.finish_reason,
            )
            return resp

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 800,
    ) -> AsyncIterator[str]:
        """Yield text deltas as they arrive. Feeds the TTS streaming path so the
        user hears the first word ~300ms after the model starts generating,
        instead of waiting for the full completion.

        Anthropic streaming is not wired — the adapter raises NotImplementedError.
        Hot-path callers should pick an OpenAI or Groq model.
        """
        mdl = model or self._settings.openai_planner_model
        adapter = self._adapter_for(mdl)
        with span("llm.stream", model=mdl, provider=adapter.provider, n_messages=len(messages)):
            async for piece in adapter.stream(
                model=mdl,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield piece


_gateway: LLMGateway | None = None


def llm() -> LLMGateway:
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway
