from __future__ import annotations

import asyncio
from typing import Any

from mira.agents._dispatch import run_tool_calls
from mira.agents._history import prepend_history
from mira.agents.base import Agent
from mira.config.settings import get_settings
from mira.runtime.llm import Message, llm
from mira.runtime.registry import registry
from mira.runtime.schemas import (
    AgentRequest,
    AgentResponse,
    AgentStatus,
)
from mira.runtime.tracing import span

_SYSTEM = """\
You are MIRA's Device specialist. You control the Mac itself: music, SMS /
iMessage, Contacts, weather, Maps, Files/Finder, system settings (volume,
brightness, mute, display sleep), and macOS apps (open, activate, quit).

Operating rules:
- Pick the right tool tag for the request:
  * music.* — play/pause/resume/stop/status for music (YouTube-backed).
  * messages.* — send iMessage/SMS, read recent threads.
  * contacts.lookup — ALWAYS call before sending a message to a name.
    Never guess a phone number or email.
  * weather.current / weather.forecast — weather by city.
  * maps.directions / maps.search — open Apple Maps.
  * files.open / files.reveal / files.search — Finder + Spotlight.
  * system.* — volume, brightness, mute, display sleep.
  * app.* — open / activate / quit apps.
- Destructive actions (send a message, quit an app) will raise a
  confirmation automatically. Keep your spoken reply clean.
- If a tool returns `ok: false`, read its `error` aloud verbatim — do NOT
  fabricate a success.
- Speak in 1-2 short TTS-ready sentences. No markdown, no lists read aloud.
- For numbers, spell as a person would say them ("volume to eighty",
  "seventy-two degrees"). Never put hyphens between digits.
- If the user says "message Sam that I'll be late" and contacts.lookup
  returns multiple matches, ask one short clarifying question.
"""


class DeviceAgent(Agent):
    name = "device"
    purpose = (
        "Controls the user's Mac and its native apps. "
        "Use for: 'play [song/artist/playlist]', 'pause music', 'volume up/down', "
        "'mute', 'text Mom I'm on my way' (iMessage/SMS), 'open Safari', "
        "'quit Spotify', 'what's the weather', 'directions home', "
        "'find this file', 'brightness 50%', 'turn on dark mode'. "
        "NOT for: email or calendar or reminders (use communication), "
        "web Q&A or live news (use research), "
        "shopping or product research (use commerce)."
    )

    # Device actions are almost always 1 tool call: app.open, system.volume,
    # maps.search. Two hops covers check-then-act (volume_get → volume_set).
    # Anything longer is a sign the model is looping.
    MAX_HOPS = 2

    def __init__(self) -> None:
        self._settings = get_settings()

    def _tool_schemas(self) -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        for tag in (
            "music", "messaging", "contacts", "weather",
            "maps", "files", "system", "apps",
        ):
            schemas.extend(registry().openai_schemas(tag=tag))
        return schemas

    async def _run(self, req: AgentRequest) -> AgentResponse:
        messages: list[Message] = [
            Message(role="system", content=_SYSTEM),
        ]
        prepend_history(messages, req.context)
        messages.append(
            Message(role="user", content=req.transcript.strip() or req.goal.strip())
        )
        tools = self._tool_schemas()

        for hop in range(self.MAX_HOPS):
            with span("device.step", hop=hop, n_messages=len(messages)):

                def _call() -> Any:
                    return llm().complete(
                        messages,
                        model=self._settings.openai_planner_model,
                        temperature=0.2,
                        max_tokens=400,
                        tools=tools,
                    )

                resp = await asyncio.to_thread(_call)

            if not resp.tool_calls:
                text = (resp.text or "").strip() or "Done."
                return AgentResponse(
                    turn_id=req.turn_id,
                    agent=self.name,
                    status=AgentStatus.DONE,
                    speak=text,
                )

            messages.append(Message(role="assistant", tool_calls=resp.tool_calls))

            outcome = await run_tool_calls(
                resp.tool_calls,
                turn_id=req.turn_id,
                agent_name=self.name,
                confirmation_prompt=_confirmation_prompt,
            )
            if outcome.confirmation is not None:
                return outcome.confirmation
            messages.extend(outcome.messages)
            # Silent side-effect tools (music.play) return success without
            # narration. Short-circuit the next LLM round so the model can't
            # volunteer a "playing X" reply that talks over the audio.
            if outcome.silent:
                return AgentResponse(
                    turn_id=req.turn_id,
                    agent=self.name,
                    status=AgentStatus.DONE,
                    speak=None,
                    silent=True,
                )

        return AgentResponse(
            turn_id=req.turn_id,
            agent=self.name,
            status=AgentStatus.ERROR,
            error="device agent: max hops reached",
            speak="I got stuck on that — try rephrasing?",
        )


from mira.runtime.confirmations import prompt_for as _confirmation_prompt  # noqa: E402
