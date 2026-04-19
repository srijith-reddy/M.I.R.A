from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# Types split out from `llm.py` so `providers.py` can import them without a
# circular dep (providers are imported by LLMGateway itself).

Role = Literal["system", "user", "assistant", "tool"]
Provider = Literal["openai", "anthropic", "groq", "deepseek"]


class Message(BaseModel):
    """Chat message. Shape is a superset of OpenAI's wire format so we can
    round-trip tool calls without per-agent translation.
      * role=assistant messages may carry `tool_calls` (and no content).
      * role=tool messages must carry `tool_call_id` and `content` (the
        serialized tool result).
    Fields default to None so `model_dump(exclude_none=True)` produces clean
    OpenAI-compatible dicts.

    Anthropic-shape translation happens inside the provider adapter, not
    here — every caller inside MIRA builds messages in OpenAI shape.
    """

    role: Role
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class LLMUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Subset of prompt_tokens served from the provider's prefix cache.
    # Populated when the provider reports it (OpenAI:
    # usage.prompt_tokens_details.cached_tokens; DeepSeek:
    # usage.prompt_cache_hit_tokens; Anthropic: usage.cache_read_input_tokens).
    # Priced at a steep discount in _estimate_cost.
    cached_prompt_tokens: int = 0
    # Input tokens that were written to cache on this call (Anthropic only —
    # OpenAI/DeepSeek caching is automatic and free to write). Priced at a
    # 25% markup vs normal input.
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0


class LLMResponse(BaseModel):
    text: str
    model: str
    provider: Provider = "openai"
    usage: LLMUsage
    finish_reason: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
