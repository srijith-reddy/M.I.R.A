from __future__ import annotations

import contextvars
import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Deque

from pydantic import BaseModel

from mira.obs.logging import log_event
from mira.runtime.schemas import ToolCall, ToolResult
from mira.runtime.tracing import span

ToolFn = Callable[..., Any | Awaitable[Any]]


Summarizer = Callable[[Any], str]


# Per-turn flag: flipped to True by dispatch when a volatile tool runs.
# Orchestrator reads it at turn end to decide whether the reply is safe to
# cache. ContextVar so concurrent turns (shouldn't happen today, but might
# with text+voice) don't cross-contaminate each other's flags.
_volatile_hit: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "mira_volatile_hit", default=False
)


def volatile_tool_hit() -> bool:
    """True if any volatile tool ran in the current turn context."""
    return _volatile_hit.get()


def reset_volatile_hit() -> None:
    """Reset the flag at the start of a new turn. Orchestrator calls this."""
    _volatile_hit.set(False)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    params_model: type[BaseModel]
    fn: ToolFn
    requires_confirmation: bool
    tags: tuple[str, ...]
    # True for tools whose results go stale in seconds (live scores, news,
    # search results). Orchestrator reads this per-turn to skip reply_cache:
    # a 30s-cached "score?" answer that fires during a live game is worse
    # than the extra LLM/tool call to re-fetch.
    volatile: bool = False
    # Optional function that turns a successful tool-result `data` object
    # into a compact, LLM-friendly string. When present, agents feed this
    # back into the planner instead of the raw JSON dump — typical payoff
    # is a 60-80% token reduction on calendar/web-search/reminder-list
    # results that would otherwise be 4-8k tokens of structured noise.
    summarizer: Summarizer | None = field(default=None)
    # Short TTS-friendly acknowledgement spoken after a confirmed mutation
    # resumes successfully. Each tool owns its own voice — "Deleted.",
    # "Forgotten.", etc. — so the orchestrator doesn't carry a phrase table.
    success_phrase: str = "Done."

    def openai_schema(self) -> dict[str, Any]:
        """OpenAI function-tool schema. Pydantic gives us JSON schema for free.

        OpenAI/DeepSeek require tool names to match ^[a-zA-Z0-9_-]+$, so dots
        in our internal names (e.g. `browser.navigate`) are translated to
        underscores at the LLM boundary. Dispatch reverses this."""
        return {
            "type": "function",
            "function": {
                "name": self.name.replace(".", "_"),
                "description": self.description,
                "parameters": self.params_model.model_json_schema(),
            },
        }


class ToolRegistry:
    """Single source of truth for every tool an agent can call.

    Why centralize:
      * Typed args (pydantic) — no ad-hoc dict validation at every call site.
      * Confirmation gate is declarative, not a scattered convention.
      * Tracing and structured tool.* events happen in one place.
      * Schema export for LLM tool-calling is generated, not hand-maintained.
    """

    # Alert when a tool's error rate over the last N calls exceeds this —
    # catches upstream outages (Brave down, Playwright crashed) before the
    # user rage-quits. Cooldown prevents a sustained outage from spamming
    # the log every single call.
    _ALERT_WINDOW = 20
    _ALERT_THRESHOLD = 0.2
    _ALERT_COOLDOWN_S = 300.0

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        # Per-tool counters. In-memory only; surfaced via `metrics()` and
        # reset by the menubar UI if it ever exposes a "reset stats" button.
        # Cheap enough to update on every dispatch that we don't guard it.
        self._metrics: dict[str, dict[str, float]] = {}
        # Recent ok/err outcomes per tool for the rolling error-rate alert.
        self._recent: dict[str, Deque[bool]] = {}
        self._last_alert_ts: dict[str, float] = {}

    def _record_metric(self, tool_name: str, *, latency_ms: int, ok: bool) -> None:
        m = self._metrics.setdefault(
            tool_name,
            {"calls": 0, "errors": 0, "total_ms": 0.0, "max_ms": 0.0},
        )
        m["calls"] += 1
        if not ok:
            m["errors"] += 1
        m["total_ms"] += float(latency_ms)
        if latency_ms > m["max_ms"]:
            m["max_ms"] = float(latency_ms)
        self._check_error_rate(tool_name, ok)

    def _check_error_rate(self, tool_name: str, ok: bool) -> None:
        window = self._recent.setdefault(
            tool_name, deque(maxlen=self._ALERT_WINDOW)
        )
        window.append(ok)
        if len(window) < self._ALERT_WINDOW:
            return
        error_rate = sum(1 for v in window if not v) / len(window)
        if error_rate <= self._ALERT_THRESHOLD:
            return
        now = time.time()
        if now - self._last_alert_ts.get(tool_name, 0.0) < self._ALERT_COOLDOWN_S:
            return
        self._last_alert_ts[tool_name] = now
        log_event(
            "tool.error_rate_alert",
            level="warn",
            tool=tool_name,
            error_rate=round(error_rate, 3),
            window=self._ALERT_WINDOW,
        )

    def metrics(self) -> dict[str, dict[str, float]]:
        """Snapshot of per-tool call/error/latency counters. Safe to call
        from any thread — returns a shallow copy so callers can iterate
        without tripping on concurrent mutation."""
        return {
            name: {
                "calls": m["calls"],
                "errors": m["errors"],
                "avg_ms": (m["total_ms"] / m["calls"]) if m["calls"] else 0.0,
                "max_ms": m["max_ms"],
                "error_rate": (m["errors"] / m["calls"]) if m["calls"] else 0.0,
            }
            for name, m in self._metrics.items()
        }

    def register(
        self,
        name: str,
        *,
        description: str,
        params: type[BaseModel],
        requires_confirmation: bool = False,
        tags: tuple[str, ...] = (),
        summarizer: Summarizer | None = None,
        volatile: bool = False,
        success_phrase: str = "Done.",
    ) -> Callable[[ToolFn], ToolFn]:
        def _decorator(fn: ToolFn) -> ToolFn:
            if name in self._tools:
                raise ValueError(f"tool already registered: {name}")
            self._tools[name] = ToolSpec(
                name=name,
                description=description,
                params_model=params,
                fn=fn,
                requires_confirmation=requires_confirmation,
                tags=tuple(tags),
                volatile=volatile,
                summarizer=summarizer,
                success_phrase=success_phrase,
            )
            return fn

        return _decorator

    def format_result(
        self,
        tool_name: str,
        result: "ToolResult",
        *,
        max_chars: int = 12000,
    ) -> str:
        """Turn a ToolResult into the string content an agent feeds back to
        the planner. When a summarizer is registered and the call succeeded,
        use it — otherwise fall back to JSON dump + truncate.

        Centralizing here means every agent benefits from a new summarizer
        the moment it's registered, without editing per-agent dispatch code.
        """
        if not result.ok:
            payload: Any = {"ok": False, "error": result.error}
        else:
            spec = self.get(tool_name)
            if spec is not None and spec.summarizer is not None:
                try:
                    summary = spec.summarizer(result.data)
                    if isinstance(summary, str) and summary.strip():
                        if len(summary) > max_chars:
                            summary = summary[:max_chars] + "...[truncated]"
                        return summary
                except Exception:
                    # Summarizer bugs must not kill the turn — fall back to JSON.
                    pass
            payload = result.data
        try:
            text = json.dumps(payload, default=str)
        except Exception:
            text = str(payload)
        if len(text) > max_chars:
            text = text[:max_chars] + "...[truncated]"
        return text

    def get(self, name: str) -> ToolSpec | None:
        spec = self._tools.get(name)
        if spec is not None or "_" not in name:
            return spec
        for candidate_name, candidate_spec in self._tools.items():
            if candidate_name.replace(".", "_") == name:
                return candidate_spec
        return None

    def list(self, *, tag: str | None = None) -> list[ToolSpec]:
        items = list(self._tools.values())
        if tag is not None:
            items = [t for t in items if tag in t.tags]
        return items

    def openai_schemas(self, *, tag: str | None = None) -> list[dict[str, Any]]:
        return [t.openai_schema() for t in self.list(tag=tag)]

    async def dispatch(self, call: ToolCall) -> ToolResult:
        """Validate args, invoke, time, wrap into a ToolResult. Never raises —
        errors are packed into `ToolResult.ok=False` so callers can keep
        composing without try/except per call."""
        t0 = time.perf_counter()
        spec = self._tools.get(call.tool)
        if spec is None and "_" in call.tool:
            for candidate_name, candidate_spec in self._tools.items():
                if candidate_name.replace(".", "_") == call.tool:
                    spec = candidate_spec
                    call.tool = candidate_name
                    break
        if spec is None:
            return ToolResult(
                call_id=call.call_id,
                ok=False,
                error=f"unknown tool: {call.tool}",
                latency_ms=0,
            )

        try:
            args_obj = spec.params_model(**call.args)
        except Exception as exc:
            return ToolResult(
                call_id=call.call_id,
                ok=False,
                error=f"arg validation failed: {exc}",
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

        with span(
            "tool.dispatch",
            tool=call.tool,
            call_id=call.call_id,
            requires_confirmation=spec.requires_confirmation,
        ):
            try:
                result = await spec.fn(args_obj)
            except Exception as exc:
                log_event(
                    "tool.error", tool=call.tool, call_id=call.call_id, error=repr(exc)
                )
                lat = int((time.perf_counter() - t0) * 1000)
                self._record_metric(call.tool, latency_ms=lat, ok=False)
                return ToolResult(
                    call_id=call.call_id,
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                    latency_ms=lat,
                )

        artifacts: list[str] = []
        data: Any = result
        if isinstance(result, dict) and "artifacts" in result:
            raw = result.get("artifacts") or []
            if isinstance(raw, list):
                artifacts = [str(a) for a in raw]

        # Any mutation that a cached reply might have referenced must bust
        # the reply cache — otherwise "what are my reminders" would keep
        # returning the pre-create list for up to 30s after a new one lands.
        # Import here to avoid a circular dep at module load.
        if _is_mutating(call.tool):
            from mira.runtime import reply_cache

            reply_cache.invalidate()

        # Record volatile hits for the orchestrator's reply-cache gate.
        # Checked after dispatch so a tool that errors still counts — the
        # user's next utterance likely retries the same query, and we don't
        # want a stale success from 25s ago to mask the retry.
        if spec.volatile:
            _volatile_hit.set(True)

        lat = int((time.perf_counter() - t0) * 1000)
        # Treat `{"ok": False, ...}` shaped payloads as errors for metrics,
        # even though the tool ran cleanly. That's the convention every
        # network-facing tool uses to signal "I ran but the upstream
        # refused" — it belongs in the error rate.
        payload_ok = not (isinstance(data, dict) and data.get("ok") is False)
        self._record_metric(call.tool, latency_ms=lat, ok=payload_ok)
        return ToolResult(
            call_id=call.call_id,
            ok=True,
            data=data,
            artifacts=artifacts,
            latency_ms=lat,
        )


_MUTATING_PREFIXES = (
    "reminder.create", "reminder.complete", "reminder.delete",
    "memory.remember", "memory.forget",
    "messages.send",
    "music.play", "music.pause", "music.resume", "music.stop",
    "system.volume_set", "system.mute", "system.brightness",
    "app.open", "app.quit",
    "browser.click", "browser.press", "browser.fill", "browser.navigate",
)


def _is_mutating(tool_name: str) -> bool:
    return any(tool_name == p or tool_name.startswith(p) for p in _MUTATING_PREFIXES)


_registry: ToolRegistry | None = None


def registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


def tool(
    name: str,
    *,
    description: str,
    params: type[BaseModel],
    requires_confirmation: bool = False,
    tags: tuple[str, ...] = (),
    summarizer: Summarizer | None = None,
    volatile: bool = False,
    success_phrase: str = "Done.",
) -> Callable[[ToolFn], ToolFn]:
    """Convenience decorator so tool modules don't import `registry()` directly."""
    return registry().register(
        name,
        description=description,
        params=params,
        requires_confirmation=requires_confirmation,
        tags=tags,
        summarizer=summarizer,
        volatile=volatile,
        success_phrase=success_phrase,
    )

