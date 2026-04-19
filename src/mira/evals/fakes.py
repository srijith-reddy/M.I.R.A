from __future__ import annotations

import json
from collections import deque
from typing import Any

from mira.runtime import llm as llm_mod
from mira.runtime.llm import LLMResponse, LLMUsage, Message


class FakeLLMGateway:
    """Deterministic stand-in for `LLMGateway`.

    Tests push scripted `LLMResponse` objects onto a FIFO queue; each
    `complete()` call pops one and returns it. This is sufficient to exercise
    every control-flow path in the agents (router decisions, tool-calling
    loops, confirmation gates) without touching the network.

    We do NOT fake `stream()` here — Batch 7 evals target control flow, not
    streaming UX. When we start grading TTS pacing we'll extend this.
    """

    def __init__(self) -> None:
        self._queue: deque[LLMResponse] = deque()
        self.calls: list[dict[str, Any]] = []

    def push(
        self,
        *,
        text: str = "",
        tool_calls: list[dict[str, Any]] | None = None,
        model: str = "gpt-4o-mini",
        finish_reason: str | None = None,
    ) -> None:
        self._queue.append(
            LLMResponse(
                text=text,
                model=model,
                provider="openai",
                usage=LLMUsage(),
                finish_reason=finish_reason,
                tool_calls=list(tool_calls or []),
            )
        )

    def push_json(self, payload: dict[str, Any], *, model: str = "gpt-4o-mini") -> None:
        self.push(text=json.dumps(payload), model=model)

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
        self.calls.append(
            {
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "tools": tools,
                "response_format": response_format,
                "messages": [m.model_dump(exclude_none=True) for m in messages],
            }
        )
        if not self._queue:
            raise AssertionError(
                "FakeLLMGateway: ran out of scripted responses "
                f"(call #{len(self.calls)}). "
                "Push one with `fake.push(...)` before the agent asks for it."
            )
        return self._queue.popleft()

    async def stream(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise NotImplementedError(
            "FakeLLMGateway.stream is not implemented; tests should target complete()."
        )


_prev_gateway: Any | None = None
_patched = False


def install_fake_llm() -> FakeLLMGateway:
    """Swap the module-level LLM singleton for a FakeLLMGateway.

    Safe to call in `setup` fixtures; pair with `restore_llm()` in teardown so
    tests that hit the real gateway aren't polluted by stale fakes."""
    global _prev_gateway, _patched
    fake = FakeLLMGateway()
    if not _patched:
        _prev_gateway = llm_mod._gateway
        _patched = True
    llm_mod._gateway = fake  # type: ignore[assignment]
    return fake


def restore_llm() -> None:
    global _prev_gateway, _patched
    if _patched:
        llm_mod._gateway = _prev_gateway  # type: ignore[assignment]
        _prev_gateway = None
        _patched = False
