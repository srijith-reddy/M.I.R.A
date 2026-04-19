from __future__ import annotations

import asyncio
import json
from typing import Any

from mira.agents._card_extract import spawn_card_extractor
from mira.agents._history import prepend_history
from mira.agents.base import Agent
from mira.config.settings import get_settings
from mira.obs.logging import log_event
from mira.runtime.llm import Message, llm
from mira.runtime.registry import ToolSpec, registry
from mira.runtime.schemas import (
    AgentRequest,
    AgentResponse,
    AgentStatus,
    Confirmation,
    ToolCall,
    ToolResult,
)
from mira.runtime.tracing import span

_SYSTEM = """\
You are MIRA's Browser Action specialist. You control a real Chromium
browser on the user's machine via the `browser.*` tools. Plan carefully and
keep the user's time cheap.

Operating rules:
- Always `browser.navigate` first. Never guess that a page is already loaded.
- Before any `browser.click` or `browser.press`, call `browser.read_page`
  and confirm the selector exists in the text. Do not invent selectors.
- Prefer `browser.search_google` when you don't know the exact URL.
- For static pages (articles, docs, blog posts, product pages without heavy
  JavaScript), prefer `web.fetch` over `browser.navigate` + `browser.read_page`.
  It's ~10x faster and runs trafilatura for clean main-content extraction.
  Fall back to `browser.navigate` only when the site needs JS (SPAs, logged-in
  dashboards, anything that returns an empty body on a plain GET).
- When using `web.search`, pass `trust_mode` that matches the user's intent:
  `news` for current events, `commerce` for shopping, `booking` for travel or
  tickets, `reference` for docs/code, `default` otherwise. This biases the
  ranking toward known-reputable sources and demotes content farms.
- When you have the information the user asked for, stop calling tools and
  produce one short, spoken answer (1–2 sentences, TTS-ready). No URLs read
  aloud, no markdown, no lists.
- TTS formatting: write numbers the way a person would say them aloud.
  Scores: "126 to 121" not "126-121". Times: "7:30 PM" is fine. Years:
  "twenty twenty-six" over "2026" only when it reads more naturally.
  Never use hyphens between numbers (the voice spells each digit).
- Team names: be specific. "LA Lakers" and "LA Clippers" are different
  teams; never collapse them to "LA". If the page is ambiguous, read
  another line before committing to an answer.
- Side-effect tools (`click`, `press`) will be gated by user confirmation —
  that is handled by the runtime. You still issue the call normally.
- Budget: at most a handful of tool calls per turn. If a site is fighting
  you, summarize what you saw and ask the user for guidance.
- If a tool returns an error (e.g. the browser failed to launch), do NOT
  invent the answer from your training data. Reply honestly: "I couldn't
  reach the web just now" or similar, and stop. Fabricating a score,
  schedule, or price is never acceptable — a failed lookup is fine, a
  confident lie is not.
"""


class BrowserActionAgent(Agent):
    name = "browser"
    purpose = (
        "Drives a real Chromium browser to act on a specific named site — "
        "opens URLs, reads pages, fills forms, clicks buttons. "
        "Use for: 'open my GitHub notifications', 'log in to X and download Y', "
        "'fill this form on site Z', 'what does this page say'. "
        "NOT for: shopping or product recommendations (use commerce), "
        "general knowledge or live news (use research), "
        "email/calendar/reminders (use communication)."
    )

    MAX_HOPS = 4

    def __init__(self) -> None:
        self._settings = get_settings()

    def _tool_schemas(self) -> list[dict[str, Any]]:
        schemas = registry().openai_schemas(tag="browser")
        # If a web search tool is registered (Brave), expose it too — it's
        # strictly faster than spinning up the browser for raw fact lookups.
        if registry().get("web.search") is not None:
            schemas.extend(registry().openai_schemas(tag="web"))
        return schemas

    async def _dispatch_tool(self, tc: dict[str, Any]) -> tuple[ToolResult, ToolSpec | None]:
        fn = tc.get("function") or {}
        name = fn.get("name") or ""
        args_raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        except Exception:
            args = {}
        spec = registry().get(name)
        call = ToolCall(tool=name, args=args, requires_confirmation=bool(
            spec.requires_confirmation if spec else False
        ))
        call.call_id = tc.get("id") or call.call_id  # prefer LLM's id for round-trip
        result = await registry().dispatch(call)
        return result, spec

    def _tool_result_message(
        self, call_id: str, tool_name: str, result: ToolResult
    ) -> Message:
        # Delegates summarization — tools that registered a `summarizer`
        # (browser.read_page, browser.search_google, web.*) produce compact
        # prose instead of the raw JSON dump, saving a large fraction of
        # the per-hop token budget on research turns.
        text = registry().format_result(tool_name, result)
        return Message(role="tool", tool_call_id=call_id, content=text)

    def _confirmation_prompt(self, tool_name: str, args: dict[str, Any]) -> str:
        from mira.runtime.confirmations import prompt_for
        return prompt_for(tool_name, args)

    async def _run(self, req: AgentRequest) -> AgentResponse:
        messages: list[Message] = [
            Message(role="system", content=_SYSTEM),
        ]
        prepend_history(messages, req.context)
        messages.append(
            Message(role="user", content=req.transcript.strip() or req.goal.strip())
        )
        tools = self._tool_schemas()
        if not tools:
            return AgentResponse(
                turn_id=req.turn_id,
                agent=self.name,
                status=AgentStatus.ERROR,
                error="no browser tools registered",
                speak="My browser isn't wired up yet.",
            )

        for hop in range(self.MAX_HOPS):
            with span("browser_agent.step", hop=hop, n_messages=len(messages)):

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
                # Model is done reasoning; speak the final text.
                text = (resp.text or "").strip() or "I finished but didn't produce a reply."
                # Kick off structured card extraction in the background so
                # TTS isn't delayed. The extractor emits ui.card itself via
                # log_event when Haiku returns — usually ~300-500ms after
                # the voice reply has already started playing.
                spawn_card_extractor(
                    agent=self.name,
                    turn_id=req.turn_id,
                    transcript=req.transcript,
                    reply=text,
                    domain_hint="web research and shopping",
                )
                return AgentResponse(
                    turn_id=req.turn_id,
                    agent=self.name,
                    status=AgentStatus.DONE,
                    speak=text,
                )

            # Echo the assistant's tool_call message so the next step has context.
            messages.append(
                Message(role="assistant", tool_calls=resp.tool_calls)
            )

            for tc in resp.tool_calls:
                fn = tc.get("function") or {}
                name = fn.get("name") or ""
                args_raw = fn.get("arguments") or "{}"
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except Exception:
                    args = {}
                spec = registry().get(name)

                # Confirmation gate — bubble up a NEED_CONFIRMATION response with
                # the specific ToolCall the user is being asked to approve. The
                # orchestrator / Supervisor decides how to resolve it.
                if spec is not None and spec.requires_confirmation:
                    pending = ToolCall(
                        tool=name,
                        args=args,
                        requires_confirmation=True,
                        call_id=tc.get("id") or "",
                    )
                    log_event(
                        "browser_agent.confirmation_required",
                        tool=name,
                        args=args,
                    )
                    return AgentResponse(
                        turn_id=req.turn_id,
                        agent=self.name,
                        status=AgentStatus.NEED_CONFIRMATION,
                        confirmation=Confirmation(
                            prompt=self._confirmation_prompt(name, args),
                            action=pending,
                        ),
                    )

                result, _ = await self._dispatch_tool(tc)
                messages.append(
                    self._tool_result_message(tc.get("id") or "", name, result)
                )

        # Hop budget exhausted.
        return AgentResponse(
            turn_id=req.turn_id,
            agent=self.name,
            status=AgentStatus.ERROR,
            error="browser agent: max hops reached",
            speak="I couldn't finish that in the steps I had. Want to narrow it down?",
        )

