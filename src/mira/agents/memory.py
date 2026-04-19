from __future__ import annotations

from typing import Any

from mira.agents.base import Agent
from mira.obs.logging import log_event
from mira.runtime.bus import bus
from mira.runtime.memory import memory
from mira.runtime.schemas import AgentRequest, AgentResponse, AgentStatus

# Turn routes whose content has no retrieval value later. Recording them
# burns an OpenAI embed call and pollutes cosine recall with trivial,
# near-duplicate rows ("pause music", "mute", "what time is it"). The
# user-facing session_state / dashboard rows still get written upstream
# via record_turn — only the embedded episodes row is skipped.
_EPISODE_SKIP_VIA: frozenset[str] = frozenset({
    "fast-path",
    "smalltalk",
})


class MemoryAgent(Agent):
    """Background writer: every completed turn becomes one episode row.

    Lives off the hot path — subscribes to `turn.completed` and lets the bus
    dispatch deliver the payload. An embedding call adds ~100–200ms against
    OpenAI, so keeping this out of the reply latency matters more than it
    might sound. The user hears TTS finish while this is still running.
    """

    name = "memory"
    purpose = "Writes episodic memory asynchronously. Never called in the hot path."

    def __init__(self) -> None:
        self._subscribed = False

    def start(self) -> None:
        if self._subscribed:
            return
        bus().subscribe("turn.completed", self._on_turn_completed)
        self._subscribed = True
        log_event("memory.subscribed")

    async def _on_turn_completed(self, _topic: str, payload: dict[str, Any]) -> None:
        turn_id = payload.get("turn_id")
        transcript = (payload.get("transcript") or "").strip()
        reply = (payload.get("reply") or "").strip()
        if not turn_id or not transcript:
            return
        via = str(payload.get("via") or "")
        # Trivial routes (regex fast-path, smalltalk) and cache-served replies
        # shouldn't create embedded episodes. Cache hits already skip the bus
        # publish upstream, but belt-and-braces here in case that changes.
        if via in _EPISODE_SKIP_VIA or via.startswith("cached:"):
            log_event("memory.skip_episode", turn_id=turn_id, via=via)
            return
        try:
            memory().record_episode(
                turn_id=turn_id,
                transcript=transcript,
                reply=reply,
                status=str(payload.get("status") or ""),
                via=str(payload.get("via") or ""),
            )
        except Exception as exc:
            # Memory is best-effort — a blip here must not affect the user.
            log_event("memory.record_error", error=repr(exc), turn_id=turn_id)

    async def _run(self, req: AgentRequest) -> AgentResponse:
        # Not used in the hot path, but Agent.handle wants a concrete impl.
        return AgentResponse(
            turn_id=req.turn_id,
            agent=self.name,
            status=AgentStatus.DONE,
            speak=None,
        )
