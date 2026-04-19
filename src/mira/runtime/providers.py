from __future__ import annotations

from typing import Any, AsyncIterator, Protocol

from mira.runtime.llm_types import LLMResponse, LLMUsage, Message, Provider

# Per-request wall-clock cap. A planner hop that blocks 30s kills the voice
# UX — rather leave the loop early and speak a partial answer than wait for
# a silent provider to eventually respond. Both OpenAI and Anthropic SDKs
# accept a per-client `timeout=` that applies to every request.
_LLM_TIMEOUT_S = 15.0

# Provider adapters. Each one owns its own SDK client, its own message
# translation, and its own response shape → `LLMResponse` mapping.
#
# Why this shape:
#   * `LLMGateway.complete()` / `.stream()` have the same signature for every
#     caller in the codebase. The switch in providers must not leak into
#     orchestrator / router / agent code.
#   * `Message` stays OpenAI-shaped inside MIRA. Each adapter translates at
#     the boundary. Trying to unify three vendors' tool-call formats inside
#     our own schema is a boiling-ocean job with minimal payoff.
#
# Not in scope for Batch 11:
#   * Anthropic streaming (`stream()` on the Anthropic adapter raises).
#     Voice hot path uses OpenAI or Groq for TTS-streamed replies; Anthropic
#     is reserved for supervisor/planner use where a single `complete()`
#     landing after 400-800ms is acceptable.
#   * Retries + fallback chains (e.g. "if Anthropic 529, retry OpenAI").
#     Add those later with explicit per-route policy.


class _ProviderAdapter(Protocol):
    """Every adapter implements complete() synchronously (matches OpenAI SDK
    shape) and stream() as an async generator of text deltas."""

    provider: Provider

    def complete(
        self,
        *,
        model: str,
        messages: list[Message],
        temperature: float,
        max_tokens: int,
        tools: list[dict[str, Any]] | None,
        response_format: dict[str, Any] | None,
    ) -> LLMResponse: ...

    def stream(
        self,
        *,
        model: str,
        messages: list[Message],
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str]: ...


# ---------- OpenAI-shape base (also serves Groq via base_url override) ----------


class _OpenAICompatAdapter:
    """Shared implementation for OpenAI-API-compatible backends. Groq is one
    of these — they literally expose `v1/chat/completions` at
    https://api.groq.com/openai/v1 with identical request/response shapes.
    Keeping the code in one class means bug fixes and new fields only need
    to land in one place."""

    def __init__(
        self,
        *,
        provider: Provider,
        api_key: str,
        base_url: str | None = None,
    ) -> None:
        self.provider: Provider = provider
        self._api_key = api_key
        self._base_url = base_url
        self._sync: Any | None = None
        self._async: Any | None = None

    def _sync_client(self) -> Any:
        if self._sync is None:
            from openai import OpenAI

            if self._base_url:
                self._sync = OpenAI(
                    api_key=self._api_key,
                    base_url=self._base_url,
                    timeout=_LLM_TIMEOUT_S,
                )
            else:
                self._sync = OpenAI(api_key=self._api_key, timeout=_LLM_TIMEOUT_S)
        return self._sync

    def _async_client(self) -> Any:
        if self._async is None:
            from openai import AsyncOpenAI

            if self._base_url:
                self._async = AsyncOpenAI(
                    api_key=self._api_key,
                    base_url=self._base_url,
                    timeout=_LLM_TIMEOUT_S,
                )
            else:
                self._async = AsyncOpenAI(
                    api_key=self._api_key, timeout=_LLM_TIMEOUT_S
                )
        return self._async

    def complete(
        self,
        *,
        model: str,
        messages: list[Message],
        temperature: float,
        max_tokens: int,
        tools: list[dict[str, Any]] | None,
        response_format: dict[str, Any] | None,
    ) -> LLMResponse:
        client = self._sync_client()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
        if response_format:
            # Groq supports JSON mode; some Groq models don't. We pass through
            # and let the SDK surface errors if the user picked an incompatible
            # combo — cheaper than maintaining a compatibility matrix here.
            kwargs["response_format"] = response_format

        resp = client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        msg = choice.message
        text = msg.content or ""

        raw_tool_calls = getattr(msg, "tool_calls", None) or []
        tool_calls_out: list[dict[str, Any]] = []
        for tc in raw_tool_calls:
            fn = getattr(tc, "function", None)
            tool_calls_out.append(
                {
                    "id": getattr(tc, "id", None),
                    "type": "function",
                    "function": {
                        "name": getattr(fn, "name", None) if fn else None,
                        "arguments": getattr(fn, "arguments", None) if fn else None,
                    },
                }
            )

        usage = getattr(resp, "usage", None)
        pt = getattr(usage, "prompt_tokens", 0) if usage else 0
        ct = getattr(usage, "completion_tokens", 0) if usage else 0
        # Cached-input reporting is provider-specific:
        #   OpenAI   → usage.prompt_tokens_details.cached_tokens (auto prefix cache, >1024 toks)
        #   DeepSeek → usage.prompt_cache_hit_tokens             (auto prefix cache, 64-tok blocks)
        # Both are charged at ~10-25% of normal input; see _estimate_cost.
        cached = 0
        if usage is not None:
            details = getattr(usage, "prompt_tokens_details", None)
            if details is not None:
                cached = getattr(details, "cached_tokens", 0) or 0
            if not cached:
                cached = getattr(usage, "prompt_cache_hit_tokens", 0) or 0

        return LLMResponse(
            text=text,
            model=model,
            provider=self.provider,
            usage=LLMUsage(
                prompt_tokens=pt,
                completion_tokens=ct,
                cached_prompt_tokens=cached,
                cost_usd=0.0,
            ),
            finish_reason=getattr(choice, "finish_reason", None),
            tool_calls=tool_calls_out,
        )

    async def stream(
        self,
        *,
        model: str,
        messages: list[Message],
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        client = self._async_client()
        stream = await client.chat.completions.create(
            model=model,
            messages=[m.model_dump(exclude_none=True) for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            piece = getattr(delta, "content", None)
            if piece:
                yield piece


# ---------- Anthropic ----------


class _AnthropicAdapter:
    """Anthropic's Messages API.

    Translation rules (OpenAI ↔ Anthropic):
      * `role=system` messages → top-level `system` string (concatenated
        if multiple). Anthropic forbids system in the messages array.
      * `role=tool` messages → a `user` message whose content is a
        `tool_result` block with the matching `tool_use_id`.
      * Assistant `tool_calls` → `tool_use` content blocks. We emit only
        the text we need to round-trip; full tool-chain loops on Anthropic
        aren't wired up here (no agent currently selects Anthropic for
        tool chains in Batch 11).
      * `response_format=json_object` is OpenAI-specific — ignored. Callers
        that need strict JSON from Anthropic must prompt for it directly.
    """

    provider: Provider = "anthropic"

    def __init__(self, *, api_key: str) -> None:
        self._api_key = api_key
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            from anthropic import Anthropic

            self._client = Anthropic(api_key=self._api_key, timeout=_LLM_TIMEOUT_S)
        return self._client

    def _translate(self, messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
        system_parts: list[str] = []
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                if m.content:
                    system_parts.append(m.content)
                continue
            if m.role == "tool":
                # Anthropic takes tool_result inside a user message.
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id or "",
                                "content": m.content or "",
                            }
                        ],
                    }
                )
                continue
            if m.role == "assistant" and m.tool_calls:
                blocks: list[dict[str, Any]] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    fn = tc.get("function", {}) or {}
                    import json as _json

                    args_raw = fn.get("arguments") or "{}"
                    try:
                        args_obj = _json.loads(args_raw)
                    except Exception:
                        args_obj = {"_raw": args_raw}
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id") or "",
                            "name": fn.get("name") or "",
                            "input": args_obj,
                        }
                    )
                out.append({"role": "assistant", "content": blocks})
                continue
            # Plain user / assistant text.
            out.append({"role": m.role, "content": m.content or ""})
        return "\n\n".join(system_parts), out

    def complete(
        self,
        *,
        model: str,
        messages: list[Message],
        temperature: float,
        max_tokens: int,
        tools: list[dict[str, Any]] | None,
        response_format: dict[str, Any] | None,
    ) -> LLMResponse:
        system, msgs = self._translate(messages)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            # Structured system block with ephemeral cache_control → Anthropic
            # caches the system prompt for 5 minutes, 90% discount on reads.
            # System prompts are stable across turns (agent personality,
            # operating rules), which is exactly the shape that benefits.
            kwargs["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        if tools:
            # OpenAI-shape: {"type":"function", "function":{"name","description","parameters"}}
            # Anthropic-shape: {"name","description","input_schema"}
            anth_tools = [
                {
                    "name": t.get("function", {}).get("name", ""),
                    "description": t.get("function", {}).get("description", ""),
                    "input_schema": t.get("function", {}).get("parameters", {}),
                }
                for t in tools
            ]
            # Mark the last tool with cache_control — Anthropic caches the
            # prefix up to and including this block, covering every prior
            # tool schema. Tool schemas are stable across turns, so this
            # hits on every follow-up call within the 5-minute TTL.
            if anth_tools:
                anth_tools[-1] = {
                    **anth_tools[-1],
                    "cache_control": {"type": "ephemeral"},
                }
            kwargs["tools"] = anth_tools
        # response_format is intentionally dropped — see class docstring.

        resp = self._get_client().messages.create(**kwargs)

        text_parts: list[str] = []
        tool_calls_out: list[dict[str, Any]] = []
        for block in getattr(resp, "content", []) or []:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(getattr(block, "text", "") or "")
            elif btype == "tool_use":
                import json as _json

                tool_calls_out.append(
                    {
                        "id": getattr(block, "id", "") or "",
                        "type": "function",
                        "function": {
                            "name": getattr(block, "name", "") or "",
                            "arguments": _json.dumps(
                                getattr(block, "input", {}) or {}
                            ),
                        },
                    }
                )

        usage = getattr(resp, "usage", None)
        pt = getattr(usage, "input_tokens", 0) if usage else 0
        ct = getattr(usage, "output_tokens", 0) if usage else 0
        # Anthropic reports cache activity as separate counters; input_tokens
        # already excludes both, so we report prompt_tokens as the sum to
        # keep the dashboard's "total input" number honest.
        cache_read = getattr(usage, "cache_read_input_tokens", 0) if usage else 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) if usage else 0
        cache_read = cache_read or 0
        cache_write = cache_write or 0

        return LLMResponse(
            text="".join(text_parts),
            model=model,
            provider="anthropic",
            usage=LLMUsage(
                prompt_tokens=pt + cache_read + cache_write,
                completion_tokens=ct,
                cached_prompt_tokens=cache_read,
                cache_creation_tokens=cache_write,
                cost_usd=0.0,
            ),
            finish_reason=getattr(resp, "stop_reason", None),
            tool_calls=tool_calls_out,
        )

    async def stream(
        self,
        *,
        model: str,
        messages: list[Message],
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        # Deliberately not implemented in Batch 11 — hot-path TTS streaming
        # lives on OpenAI/Groq until we have a reason to pay the translation
        # cost for Anthropic token deltas too.
        raise NotImplementedError(
            "Anthropic streaming is not wired in this build. "
            "Use an OpenAI or Groq model for the streaming path."
        )
        if False:  # pragma: no cover — satisfies the AsyncIterator protocol
            yield ""


# ---------- Provider resolution ----------


# Prefix-based model→provider map. Explicit is better than implicit: a new
# model name doesn't accidentally inherit the wrong provider, and we can see
# at a glance which families we support.
_MODEL_PREFIXES: tuple[tuple[str, Provider], ...] = (
    ("gpt-", "openai"),
    ("o1-", "openai"),
    ("o3-", "openai"),
    ("text-embedding-", "openai"),
    ("whisper-", "openai"),
    ("claude-", "anthropic"),
    # Groq hosts these OSS models; match by exact/common prefixes. If a user
    # routes a Llama model to OpenAI-compatible endpoint elsewhere, they can
    # override via `provider_for` at the call site.
    ("llama-", "groq"),
    ("mixtral-", "groq"),
    ("gemma-", "groq"),
    # DeepSeek — OpenAI-compatible endpoint at api.deepseek.com.
    # `deepseek-chat` is V3, `deepseek-reasoner` is R1.
    ("deepseek-", "deepseek"),
)


def provider_for(model: str) -> Provider:
    for prefix, prov in _MODEL_PREFIXES:
        if model.startswith(prefix):
            return prov
    # Unknown family — treat as OpenAI. Safer default because OpenAI is
    # required for embeddings regardless, so a misrouted call still has a
    # working client.
    return "openai"
