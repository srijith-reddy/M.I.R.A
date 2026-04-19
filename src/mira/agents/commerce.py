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
You are MIRA's Commerce specialist. You help the user research products,
compare prices, track orders, and set price-watch reminders. You operate a
logged-in Chromium profile — Amazon, Instacart, and most retailers already
have the user's session.

Operating rules:
- DEFAULT to `web.search` + `web.fetch` for read-only research. That
  pipeline is parallel, no-JS, and ~10x faster than driving Chromium.
  It's the right tool for every "what's the price / top N / is X in
  stock / compare A vs B" question. You read search results + scraped
  page text and synthesize from there — no browser needed.
- Only reach for `browser.navigate` when the task requires being
  logged in (user's Amazon orders, Instacart cart) or clicking
  (checkout, "Add to cart", form fill). If a logged-out stranger with
  curl could answer the question, don't open the browser.
- When calling `web.search`, always pass `trust_mode="commerce"`. The tool
  tiers results so Amazon / Target / Wirecutter / RTINGS float above random
  dropshipper and SEO-content sites. For travel/hotel/ticket searches use
  `trust_mode="booking"` instead.
- Never place an order without an explicit "yes" on the final step. The
  runtime enforces confirmation on `browser.click` and `browser.press`; your
  prompt copy should name the exact action ("Place the order for $X?") so
  the user isn't confirming a vague intent.
- Never pre-fill payment or shipping fields beyond what already exists in the
  logged-in session. If the site asks for new card details, stop and tell
  the user to finish in their browser.
- For price watches and restock reminders, use `reminder.create` with a
  crisp text like "Check price on <product> at <url>".
- Speak in 1–2 short sentences, TTS-ready. Quote prices with currency, not
  symbols ("nineteen dollars" is fine in speech, but "$19" is what you
  return — TTS handles the pronunciation).
- If the user's target is ambiguous ("order more coffee" — which one?), ask
  one short clarifying question before touching a retailer.
"""


class CommerceAgent(Agent):
    name = "commerce"
    purpose = (
        "Shopping, product research, price comparisons, and order tracking. "
        "Use for: 'best laptops under $1000', 'cheapest flights to LA next weekend', "
        "'is the PS5 in stock', 'track my Amazon order', 'compare iPhone 15 vs 16', "
        "'find me a blender under fifty bucks', 'watch this price and tell me when it drops'. "
        "NOT for: general facts or trivia (use research), "
        "opening a specific website (use browser), "
        "playing music or launching apps (use device)."
    )

    # Commerce needs to search candidates, visit 2-3 product pages, read
    # each, then confirm. Four hops runs out after the first site; six gives
    # room for real comparison without runaway cost.
    MAX_HOPS = 6

    def __init__(self) -> None:
        self._settings = get_settings()

    def _tool_schemas(self) -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        schemas.extend(registry().openai_schemas(tag="browser"))
        schemas.extend(registry().openai_schemas(tag="reminders"))
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
        # Accumulated across hops. Brave thumbnails get attached to card
        # rows in the post-hoc extractor — see spawn_card_extractor below.
        sources: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        for hop in range(self.MAX_HOPS):
            with span("commerce.step", hop=hop, n_messages=len(messages)):

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
                spawn_card_extractor(
                    agent=self.name,
                    turn_id=req.turn_id,
                    transcript=req.transcript,
                    reply=text,
                    domain_hint="shopping and product comparisons",
                    sources=sources,
                )
                return AgentResponse(
                    turn_id=req.turn_id,
                    agent=self.name,
                    status=AgentStatus.DONE,
                    speak=text,
                    sources=sources,
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

            # Harvest web.search hits so the background card extractor can
            # attach brand-accurate thumbnails. Dedup by URL across hops.
            for tool_name, data in outcome.raw_results:
                if tool_name != "web.search" or not isinstance(data, dict):
                    continue
                for r in data.get("results") or []:
                    url = r.get("url")
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    sources.append({
                        "title": r.get("title") or "",
                        "url": url,
                        "thumbnail": r.get("thumbnail"),
                    })

        return AgentResponse(
            turn_id=req.turn_id,
            agent=self.name,
            status=AgentStatus.ERROR,
            error="commerce agent: max hops reached",
            speak="I ran out of steps. Want to narrow it down?",
            sources=sources,
        )


from mira.runtime.confirmations import prompt_for as _confirmation_prompt  # noqa: E402
