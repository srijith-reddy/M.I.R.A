from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from mira.agents._card_extract import spawn_card_extractor
from mira.agents.base import Agent, agents
from mira.config.settings import get_settings
from mira.obs.logging import log_event
from mira.runtime.llm import Message, llm
from mira.runtime.memory import memory
from mira.runtime.schemas import (
    AgentRequest,
    AgentResponse,
    AgentStatus,
    Handoff,
)
from mira.runtime.session import recent_turns

_SYSTEM = """\
You are MIRA's Supervisor. You coordinate specialist agents to fulfill the
user's request. You never speak to the user with raw tool output; you
compose a final answer suitable for text-to-speech.

Each turn, you output one JSON object:

{
  "action": "handoff" | "speak" | "clarify",
  "to_agent": string | null,
  "goal": string | null,
  "why": string | null,
  "speak": string | null
}

Rules:
- "handoff": delegate to a specialist. Provide a concise goal (imperative
  verb phrase). Use when the request needs knowledge, research, or a tool
  chain you do not own.
- "speak": you already have everything needed; respond directly in one or
  two short, natural spoken sentences. Use for greetings, closers, and
  requests that don't require a specialist.
- "clarify": ask one short question if the user's intent is genuinely
  ambiguous, OR when you need more details to complete a multi-step
  request (e.g. "what date should I book?"). Using `clarify` keeps the
  mic open — the user's next utterance will come back as a continuation
  of this task, with the previous turn in memory. If you instead use
  `speak` to ask a question, the conversation ends and the user has to
  re-open with a new wake word, losing context. ALWAYS prefer `clarify`
  over `speak` when your text ends with a question mark.

Multi-turn continuations:
- Check `memory.recent_turns` before answering. If the most recent
  reply was a question you asked, the current transcript is the user's
  answer to it — continue that task, do not restart from scratch.
- When handing off mid-task, include the earlier turn's context in the
  `goal` field so the specialist has full background.

Routing heuristics:
- Time / today's date: the `now` field in this prompt IS ground truth.
  Speak from it directly. Never delegate a time question.
- Live facts (sports scores or schedules, weather, stock prices, news
  today, anything that changes day-to-day): handoff to `browser`. Do NOT
  send these to `research` — it has no internet and will hallucinate.
- Static knowledge (definitions, how things work, history, explanations):
  handoff to `research`.
- Email / calendar / reminders: handoff to `communication`.
- Shopping / price checks: handoff to `commerce`.
- Web actions (fill a form, click through a page): handoff to `browser`.
- Mac device control: handoff to `device`. This covers:
  * Music playback ("play <song>", pause/resume/stop music).
  * iMessage / SMS ("text Sam …"). Device agent resolves the contact.
  * Weather ("weather in Austin", "will it rain tomorrow").
  * Directions / Apple Maps ("directions to LAX").
  * Files / Finder ("open my Downloads", "find my tax pdf").
  * System settings (volume, brightness, mute, sleep the display).
  * Opening / quitting Mac apps ("open Safari", "quit Spotify").

If the memory block includes `profile.user_name`, you may address the
user by that name when it feels natural — greetings, confirmations,
acknowledgements. Never use it more than once per reply, and skip it
entirely for short utility responses (e.g. a quick time readout or unit
conversion).

The example text in this prompt is illustrative only — never quote it
verbatim. For factual questions (time, date, weather, math) you MUST
compute or look up the real answer, not invent one. If you do not have a
tool or specialist to get the real value, say so honestly rather than
guessing.

Keep JSON strictly valid. Do not add commentary outside the object.

TTS formatting for any `speak` text you produce: write numbers as a
person would say them, not as digits. Scores → "126 to 121". Never use
hyphens between numbers — the voice reads each digit separately.
"""


class SupervisorAgent(Agent):
    name = "supervisor"
    purpose = "Plans, delegates to specialists, composes the final spoken reply."

    # Supervisor orchestrates multi-tool turns: memory.recall + research.deep
    # + maybe reminder.create + compose. Three hops runs out on anything
    # beyond recall → answer. Five gives real planning headroom; beyond that
    # we're usually better off asking the user than spinning more.
    MAX_HOPS = 5

    def __init__(self) -> None:
        self._settings = get_settings()

    def _catalog_block(self) -> str:
        catalog = [
            a for a in agents().describe() if a["name"] != self.name
        ]
        return "\n".join(f"- {a['name']}: {a['purpose']}" for a in catalog)

    def _memory_block(self, transcript: str, user_id: str) -> dict[str, Any]:
        """Assemble the lightweight memory view the planner sees each hop.

        Three parts, all best-effort — any of them can be empty:
          * `profile` — stable facts (name, preferences).
          * `recent_turns` — last 3 completed turns for continuity across
            same-session utterances ("what about tomorrow?").
          * `recalled` — top-k episodes semantically similar to this turn's
            transcript. Helps with "remind me what we decided last week".

        We keep this on the Supervisor — not the Router or specialists —
        because it's where multi-turn reasoning actually lives and because
        it lets us tune the injection in one place."""
        block: dict[str, Any] = {}
        try:
            profile = memory().list_profile()
            if profile:
                block["profile"] = profile
        except Exception as exc:
            log_event("supervisor.profile_error", error=repr(exc))

        try:
            rt = recent_turns(user_id=user_id, limit=3)
            if rt:
                block["recent_turns"] = [
                    {"transcript": r.transcript, "reply": r.reply} for r in rt
                ]
        except Exception as exc:
            log_event("supervisor.recent_turns_error", error=repr(exc))

        # Skip semantic recall on trivial utterances — "what time", "thanks",
        # "hey mira". Embedding them is a waste of an API call and the top-k
        # hits are noise (short queries match everything). 4-word floor is
        # enough to filter greetings/acks while still catching real questions.
        if len(transcript.split()) < 4:
            return block

        try:
            recalled = memory().recall(transcript, k=3, user_id=user_id)
            if recalled:
                block["recalled"] = [
                    {
                        "transcript": ep.transcript,
                        "reply": ep.reply,
                        "score": round(ep.score, 3),
                    }
                    for ep in recalled
                ]
        except Exception as exc:
            log_event("supervisor.recall_error", error=repr(exc))

        return block

    async def _decide(
        self,
        transcript: str,
        *,
        context: dict[str, Any],
        memory_block: dict[str, Any],
        user_id: str = "local",
    ) -> dict[str, Any]:
        system = _SYSTEM + "\n\nAvailable specialists:\n" + self._catalog_block()
        now = datetime.now().astimezone()
        user_block = {
            "transcript": transcript,
            "context": context,
            "memory": memory_block,
            "now": {
                "iso": now.isoformat(timespec="seconds"),
                "human": now.strftime("%A, %B %-d %Y, %-I:%M %p %Z"),
            },
        }
        messages = [
            Message(role="system", content=system),
            Message(role="user", content=json.dumps(user_block)),
        ]

        def _call() -> str:
            resp = llm().complete(
                messages,
                model=self._settings.openai_planner_model,
                temperature=0.2,
                max_tokens=260,
                response_format={"type": "json_object"},
            )
            return resp.text

        raw = await asyncio.to_thread(_call)
        try:
            return json.loads(raw)
        except Exception as exc:
            log_event("supervisor.parse_error", error=repr(exc), raw=raw[:200])
            return {
                "action": "speak",
                "speak": "I had trouble thinking that through. Could you say it again?",
            }

    async def _run(self, req: AgentRequest) -> AgentResponse:
        transcript = req.transcript.strip() or req.goal.strip()
        context: dict[str, Any] = dict(req.context)
        specialist_notes: list[str] = []
        user_id = str(req.context.get("user_id") or "local")

        # Sources harvested by specialists during handoffs. When the
        # supervisor composes its own final reply (action=speak, or the
        # max-hops fallback that reuses a partial specialist note), we
        # spawn the Haiku card extractor with these so the fallback card
        # gets brand-accurate thumbnails — same tier as a successful
        # commerce/research turn. Dedup by URL across hops.
        aggregated_sources: list[dict[str, Any]] = []
        seen_source_urls: set[str] = set()

        # Build the memory view once per turn. `memory().recall()` embeds the
        # transcript via OpenAI — with MAX_HOPS=3 we were paying that 3x per
        # turn for an input that never changes mid-turn. The recalled episodes
        # and recent turns are stable within a single user utterance.
        memory_block = self._memory_block(transcript, user_id)

        # Track specialists that failed this turn. A specialist that hit
        # MAX_HOPS or errored will almost certainly do it again on the
        # same input — re-dispatching just burns the user's time and
        # tokens. Block retries on the same turn; the model can pick a
        # different specialist or speak directly.
        failed_specialists: set[str] = set()

        hops_used = 0
        for hop in range(self.MAX_HOPS):
            hops_used = hop + 1
            decision = await self._decide(
                transcript,
                context=context,
                memory_block=memory_block,
                user_id=user_id,
            )
            action = decision.get("action", "speak")

            if action == "clarify":
                msg = (decision.get("speak") or "").strip()
                log_event("supervisor.hop_count", hops=hops_used, outcome="clarify")
                return AgentResponse(
                    turn_id=req.turn_id,
                    agent=self.name,
                    status=AgentStatus.NEED_CLARIFICATION,
                    speak=msg or "Could you say that again?",
                )

            if action == "speak":
                msg = (decision.get("speak") or "").strip()
                log_event("supervisor.hop_count", hops=hops_used, outcome="speak")
                spawn_card_extractor(
                    agent=self.name,
                    turn_id=req.turn_id,
                    transcript=transcript,
                    reply=msg,
                    sources=aggregated_sources,
                )
                return AgentResponse(
                    turn_id=req.turn_id,
                    agent=self.name,
                    status=AgentStatus.DONE,
                    speak=msg or "Okay.",
                    sources=aggregated_sources,
                )

            if action == "handoff":
                target = decision.get("to_agent")
                goal = (decision.get("goal") or "").strip()
                specialist = agents().get(target) if target else None
                if specialist is None:
                    log_event("supervisor.bad_handoff", target=target)
                    # Give the model one more chance by hinting at the mistake,
                    # but don't loop forever — MAX_HOPS guards it.
                    context["last_error"] = f"unknown agent: {target}"
                    continue
                if specialist.name in failed_specialists:
                    # Model is re-picking a specialist we already proved
                    # can't handle this input. Tell it so, don't dispatch.
                    log_event(
                        "supervisor.handoff_skipped_duplicate",
                        target=specialist.name,
                    )
                    context["last_error"] = (
                        f"{specialist.name} already failed on this input "
                        f"this turn — pick a different specialist or speak directly."
                    )
                    continue

                sub_context = dict(context)
                if memory_block:
                    sub_context["memory"] = memory_block
                sub_req = AgentRequest(
                    turn_id=req.turn_id,
                    agent=specialist.name,
                    goal=goal or transcript,
                    transcript=transcript,
                    context=sub_context,
                    budget_ms=max(500, req.budget_ms - 500),
                )
                log_event(
                    "supervisor.handoff",
                    to_agent=specialist.name,
                    goal=goal,
                    hop=hop,
                )
                sub_resp = await specialist.handle(sub_req)

                # Propagate terminal or blocking statuses unchanged — the user
                # needs to see a clarification or confirmation prompt, not our
                # re-planning of it.
                if sub_resp.status in (
                    AgentStatus.NEED_CONFIRMATION,
                    AgentStatus.NEED_CLARIFICATION,
                ):
                    log_event(
                        "supervisor.hop_count",
                        hops=hops_used,
                        outcome=sub_resp.status.value if hasattr(sub_resp.status, "value") else str(sub_resp.status),
                    )
                    return AgentResponse(
                        turn_id=req.turn_id,
                        agent=self.name,
                        status=sub_resp.status,
                        speak=sub_resp.speak,
                        confirmation=sub_resp.confirmation,
                    )

                if sub_resp.status == AgentStatus.DONE and (sub_resp.speak or sub_resp.silent):
                    log_event(
                        "supervisor.hop_count",
                        hops=hops_used,
                        outcome="handoff_done",
                        agent=specialist.name,
                    )
                    return AgentResponse(
                        turn_id=req.turn_id,
                        agent=self.name,
                        status=AgentStatus.DONE,
                        speak=sub_resp.speak,
                        silent=sub_resp.silent,
                    )

                # Feed the specialist's output back as context for the next
                # planning step; this is how multi-hop chains compose without
                # the Supervisor hard-coding sequence logic.
                specialist_notes.append(
                    f"{specialist.name}: {sub_resp.speak or sub_resp.error or 'no content'}"
                )
                context["specialist_notes"] = specialist_notes
                # Harvest any web sources the specialist collected, even on
                # failure — they're still relevant to the user's question and
                # give the supervisor's fallback card brand-accurate thumbs.
                for src in sub_resp.sources:
                    url = src.get("url")
                    if not url or url in seen_source_urls:
                        continue
                    seen_source_urls.add(url)
                    aggregated_sources.append(src)
                # If the specialist failed (ERROR or REFUSED), mark it so
                # we don't re-dispatch to the same one next hop.
                if sub_resp.status in (AgentStatus.ERROR, AgentStatus.REFUSED):
                    failed_specialists.add(specialist.name)
                continue

            # Unknown action — treat as a bad plan.
            log_event("supervisor.unknown_action", action=action)
            break

        # Hops exhausted. Prefer the last specialist's reply over a canned
        # "ran out of thinking budget" message — partial truth beats a
        # user-hostile apology.
        log_event("supervisor.hop_count", hops=hops_used, outcome="max_hops")
        fallback = ""
        for note in reversed(specialist_notes):
            _, _, body = note.partition(": ")
            body = body.strip()
            if body and body != "no content":
                fallback = body
                break
        if fallback:
            spawn_card_extractor(
                agent=self.name,
                turn_id=req.turn_id,
                transcript=transcript,
                reply=fallback,
                sources=aggregated_sources,
            )
        return AgentResponse(
            turn_id=req.turn_id,
            agent=self.name,
            status=AgentStatus.ERROR if not fallback else AgentStatus.DONE,
            error="supervisor: max hops reached without final answer" if not fallback else None,
            speak=fallback or "I couldn't quite wrap that up. Want to try again?",
            sources=aggregated_sources,
        )


def make_handoff(to: str, goal: str, why: str = "") -> Handoff:
    """Helper for specialists that want to escalate back up. Not used in
    Batch 3 yet — kept here so call sites in later agents stay short."""
    return Handoff(to_agent=to, goal=goal, why=why)
