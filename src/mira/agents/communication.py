from __future__ import annotations

import asyncio
from typing import Any

from mira.agents._card_extract import spawn_card_extractor
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
You are MIRA's Communication specialist. You handle email, calendar,
reminders, and light personal-information tasks. You control a real
logged-in browser for email (the user is already signed into Gmail in
the persistent Chromium profile), read the user's macOS Calendar via
EventKit, and manage a local reminder store.

Operating rules:
- For reminders: use `reminder.*` tools directly. No browser.
- For calendar reads ("what's on today?", "when's my next meeting?"):
  use `calendar.today`, `calendar.upcoming`, or `calendar.search`. These
  read macOS Calendar.app directly — no browser needed. If a tool returns
  `status != "ok"`, read its `reason` aloud verbatim instead of guessing.
- For email: prefer the `gmail.*` tools — they hit the Gmail API directly
  and return structured data in ~300ms. Use `gmail.search` for
  "what's in my inbox", "any email from X", "unread emails" (supports
  Gmail search syntax: `from:`, `is:unread`, `newer_than:7d`, etc.);
  `gmail.read` to fetch one message's full body; `gmail.unread_count`
  for quick counts; `gmail.send` to compose. Resolve names to emails via
  `contacts.lookup` first — never guess. Only fall back to
  `browser.navigate("https://mail.google.com")` if a gmail.* call fails
  with "not authorized" or for UI tasks the API can't do (e.g. "show
  me the inbox"). Summarize email content briefly; never read full
  message bodies aloud.
- For WhatsApp: use `browser.navigate("https://web.whatsapp.com")`, then
  `browser.read_page` to see chats, and `browser.click` / `browser.type`
  to compose. The user has already signed in via the persistent profile.
  If the page shows a QR code, the session has expired — tell the user
  to re-run the WhatsApp login helper instead of trying to proceed.
- Before sending an email or deleting a reminder, explicitly confirm with
  the user. The runtime enforces this on destructive tools (`browser.click`,
  `browser.press`, `reminder.delete`) — your spoken answer should still be
  clean when confirmation is raised.
- Speak in 1–2 short sentences, TTS-ready: no markdown, no lists read aloud.
  When listing reminders, events, or emails, mention counts and the top 2-3
  items. Use human times ("at 3pm", "tomorrow morning"), not ISO strings.
- If the user's intent is ambiguous ("email Sam" — which Sam?), ask one
  short clarifying question.
"""


class CommunicationAgent(Agent):
    name = "communication"
    purpose = (
        "The user's personal email, calendar, and reminders. "
        "Use for: 'what are my reminders', 'remind me to call Mom at 3pm', "
        "'what's on my calendar today', 'when's my next meeting', "
        "'email Sam about the deck', 'check my inbox', 'delete that reminder', "
        "'whatsapp Sam I'm running late', 'check my WhatsApp'. "
        "NOT for: iMessage/SMS (use device), "
        "web search or live news (use research), "
        "shopping or price checks (use commerce)."
    )

    # Typical flows: reminder.list → speak (2), or contacts.lookup →
    # messages.send → speak (3). Calendar reads via browser may chain
    # navigate → read_page → speak. Cap at 3 so a misbehaving loop can't
    # rack up 4 LLM calls for what should be 1-2 tool dispatches.
    MAX_HOPS = 3

    def __init__(self) -> None:
        self._settings = get_settings()

    def _tool_schemas(self) -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        schemas.extend(registry().openai_schemas(tag="reminders"))
        schemas.extend(registry().openai_schemas(tag="calendar"))
        schemas.extend(registry().openai_schemas(tag="gmail"))
        schemas.extend(registry().openai_schemas(tag="contacts"))
        schemas.extend(registry().openai_schemas(tag="browser"))
        schemas.extend(registry().openai_schemas(tag="memory"))
        if registry().get("web.search") is not None:
            schemas.extend(registry().openai_schemas(tag="web"))
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
            with span("communication.step", hop=hop, n_messages=len(messages)):

                def _call() -> Any:
                    return llm().complete(
                        messages,
                        model=self._settings.openai_planner_model,
                        temperature=0.2,
                        max_tokens=500,
                        tools=tools,
                    )

                resp = await asyncio.to_thread(_call)

            if not resp.tool_calls:
                text = (resp.text or "").strip() or "Done."
                # Inbox summaries, reminder lists, upcoming-events — all
                # naturally plural. Haiku extracts structured rows in the
                # background so TTS isn't delayed.
                spawn_card_extractor(
                    agent=self.name,
                    turn_id=req.turn_id,
                    transcript=req.transcript,
                    reply=text,
                    domain_hint="reminders, calendar events, and email",
                )
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

        return AgentResponse(
            turn_id=req.turn_id,
            agent=self.name,
            status=AgentStatus.ERROR,
            error="communication agent: max hops reached",
            speak="I ran out of steps. Want to narrow it down?",
        )


from mira.runtime.confirmations import prompt_for as _confirmation_prompt  # noqa: E402
