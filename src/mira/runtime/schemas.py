from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def _short_id() -> str:
    return uuid.uuid4().hex[:12]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentStatus(str, Enum):
    DONE = "done"
    HANDOFF = "handoff"
    NEED_CONFIRMATION = "need_confirmation"
    NEED_CLARIFICATION = "need_clarification"
    # Agent ran cleanly but concluded it's the wrong one for this request
    # (no tools for live data, no session for this site, etc.). The
    # orchestrator uses this as a signal to retry via supervisor.
    REFUSED = "refused"
    ERROR = "error"


class Turn(BaseModel):
    """One end-to-end user interaction from wake-word to final TTS."""

    model_config = ConfigDict(frozen=False)

    turn_id: str = Field(default_factory=_short_id)
    user_id: str = "local"
    transcript: str
    created_at: datetime = Field(default_factory=_utcnow)
    meta: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    """A single invocation of a registered tool."""

    call_id: str = Field(default_factory=_short_id)
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    requires_confirmation: bool = False


class ToolResult(BaseModel):
    call_id: str
    ok: bool
    data: Any | None = None
    error: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    latency_ms: int = 0


class Handoff(BaseModel):
    """Supervisor → specialist delegation, or specialist → supervisor escalation."""

    to_agent: str
    goal: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    why: str = ""


class Confirmation(BaseModel):
    """Side-effect gate. The Supervisor speaks `prompt` and waits for user yes/no."""

    prompt: str
    action: ToolCall
    alternatives: list[ToolCall] = Field(default_factory=list)


class AgentRequest(BaseModel):
    """Inbound work unit for an agent."""

    turn_id: str
    agent: str
    goal: str
    transcript: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    budget_ms: int = 4000


class AgentResponse(BaseModel):
    """Outbound result from an agent run. Exactly one of handoff / confirmation / tool_calls / speak
    is expected to be populated for a given status."""

    # arbitrary_types_allowed: `speak_stream` holds an AsyncIterator which
    # pydantic can't validate. We use pydantic for this whole hierarchy's
    # schema/serialization, and streaming agents need to hand a live
    # iterator back up to the voice loop without copying through a dict.
    model_config = ConfigDict(arbitrary_types_allowed=True)

    turn_id: str
    agent: str
    status: AgentStatus
    speak: str | None = None
    # Optional token stream for TTS. When present, the voice loop feeds it
    # to tts().speak_stream() for word-by-word playback; `speak` is ignored.
    # Set by agents that can emit synthesis progressively (research synth,
    # supervisor streaming reply) to cut first-audio from ~3s to ~1s.
    speak_stream: Any | None = None
    handoff: Handoff | None = None
    confirmation: Confirmation | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    error: str | None = None
    latency_ms: int = 0
    # When True, the voice loop skips TTS for this turn. Set by agents when
    # the tool's side effect *is* the response (e.g. music starts playing —
    # narrating it only interrupts the music).
    silent: bool = False
    cost_usd: float = 0.0
    # Optional structured payload for card-based UI rendering. Agents that
    # produce list/compare/answer data (commerce, research, comms inbox)
    # populate this; voice-first agents (supervisor smalltalk) leave it None.
    # Wired behind the modality classifier — today it's metadata only, the
    # UI does not yet render from it.
    ui_payload: dict[str, Any] | None = None
    # Hint from the agent about how this reply should be delivered. The
    # modality classifier uses this as a tie-breaker, not a hard override.
    # None = let the classifier decide purely from content + transcript.
    modality_hint: Literal["voice", "hybrid", "visual", "silent"] | None = None
    # Web sources gathered by the specialist during this turn — each entry
    # is {title, url, thumbnail?}. Supervisor aggregates these across
    # handoffs so its own card extractor can attach brand-accurate
    # thumbnails on fallback turns (specialist errors, max-hops).
    sources: list[dict[str, Any]] = Field(default_factory=list)


RouterDecisionKind = Literal["direct", "supervisor", "smalltalk"]


class RouterDecision(BaseModel):
    """Tier-0 fast-router output. Either dispatches directly or escalates to Supervisor."""

    kind: RouterDecisionKind
    agent: str | None = None
    confidence: float = 0.0
    reason: str = ""
