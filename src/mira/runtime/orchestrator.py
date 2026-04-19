from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from mira.agents import agents, install_default_agents, router
from mira.agents._text import strip_markdown
from mira.obs.logging import log_event
from mira.runtime import fast_path, modality, reply_cache
from mira.runtime.bus import bus
from mira.runtime.registry import registry, reset_volatile_hit, volatile_tool_hit
from mira.runtime.schemas import (
    AgentRequest,
    AgentResponse,
    AgentStatus,
    ToolCall,
    Turn,
)
from mira.runtime.session import (
    PendingConfirmation,
    classify_confirmation,
    clear_pending,
    load_pending,
    record_turn,
    recent_turns,
    set_pending,
)
from mira.runtime.tracing import turn_context


@dataclass(frozen=True)
class TurnResult:
    turn_id: str
    transcript: str
    reply: str | None
    status: AgentStatus
    error: str | None
    via: str  # "direct:<agent>" | "supervisor" | "smalltalk" | "confirmation-resume" | "router-fallback" | "fast-path" | "cached:<orig>"
    # Optional live token stream for TTS. When present, the voice loop feeds
    # it to tts().speak_stream() and accumulates the final text after the
    # stream exhausts. `reply` stays None in this case; callers that need
    # the final text (logging, record_turn) must wait on the TTS consumer.
    reply_stream: Any | None = field(default=None)
    # Agent asked the voice loop to skip TTS — the tool's side effect is
    # the reply (see AgentResponse.silent).
    silent: bool = field(default=False)


_SMALLTALK_AGENT = "supervisor"


async def run_turn(
    transcript: str,
    *,
    user_id: str = "local",
) -> TurnResult:
    """Drive a single end-to-end turn.

    Two entry paths:
      1. Normal turn: router → agent → response. If response is
         NEED_CONFIRMATION, persist the pending call in session state and
         bubble the prompt back to the user.
      2. Confirmation-resume turn: if a pending confirmation is alive for
         this user, inspect this turn's transcript. 'yes' → dispatch the
         pending tool and speak the outcome. 'no' → clear, acknowledge.
         'unclear' → ignore resume, fall through to a normal turn; the
         pending call stays armed until it expires.
    """
    install_default_agents()
    reset_volatile_hit()
    turn = Turn(transcript=transcript, user_id=user_id)

    with turn_context(turn.turn_id):
        pending = load_pending(user_id)
        if pending is not None:
            intent = classify_confirmation(transcript)
            log_event(
                "orchestrator.pending_check",
                intent=intent,
                pending_tool=pending.tool_call.tool,
            )
            if intent == "yes":
                return await _resume_confirmed(turn, transcript, pending, user_id)
            if intent == "no":
                clear_pending(user_id)
                reply = "Okay, cancelled."
                await bus().publish(
                    "turn.completed",
                    turn_id=turn.turn_id,
                    transcript=transcript,
                    reply=reply,
                    status=AgentStatus.DONE.value,
                    via="confirmation-resume",
                )
                record_turn(
                    turn_id=turn.turn_id,
                    transcript=transcript,
                    reply=reply,
                    status=AgentStatus.DONE.value,
                    via="confirmation-resume",
                    user_id=user_id,
                )
                return TurnResult(
                    turn_id=turn.turn_id,
                    transcript=transcript,
                    reply=reply,
                    status=AgentStatus.DONE,
                    error=None,
                    via="confirmation-resume",
                )
            # intent == "unclear" → fall through. The pending stays live.

        # Regex fast-path: deterministic single-tool intents ("pause music",
        # "volume 50", "what time is it") skip both the router LLM and the
        # specialist LLM. Saves ~400-800ms and two LLM calls per match.
        # Runs before the reply cache so time/date queries always return a
        # fresh answer instead of a 30s-stale cached reply.
        if pending is None:
            fm = fast_path.match(transcript)
            if fm is not None:
                return await _run_fast_path(turn, transcript, fm, user_id)

        # Reply cache: short-TTL shortcut for "what time is it" / "thanks" /
        # any repeat utterance within 30s. Skipped when a confirmation is
        # pending (the transcript is an answer to a question, not a query).
        if pending is None:
            cached = reply_cache.get(transcript, user_id=user_id)
            if cached is not None:
                log_event(
                    "orchestrator.reply_cache_hit",
                    via=cached.via,
                    age_s=round(time.time() - (cached.expires_at - 30.0), 2),
                )
                await bus().publish(
                    "turn.completed",
                    turn_id=turn.turn_id,
                    transcript=transcript,
                    reply=cached.reply,
                    status=cached.status,
                    via=cached.via,
                )
                # Deliberately skip `record_turn` on cache hits — the
                # original turn is already in episodes, and re-embedding
                # the same content burns the OpenAI call we're trying to
                # skip in the first place.
                return TurnResult(
                    turn_id=turn.turn_id,
                    transcript=transcript,
                    reply=cached.reply,
                    status=AgentStatus.DONE,
                    error=None,
                    via=cached.via,
                )

        # Pull the last few completed turns. Serves two purposes:
        #   1. Router continuity: the most recent turn steers short
        #      follow-ups ("14 inch M5 Pro") back to the right domain.
        #   2. Specialist memory: the full 3-turn window is passed into
        #      `AgentRequest.context["recent_turns"]` so specialists can
        #      prepend it as conversation history — without this, each
        #      specialist starts cold and re-asks questions already
        #      answered one turn ago.
        prior_turn_ctx: dict[str, Any] | None = None
        recent_turns_payload: list[dict[str, Any]] = []
        try:
            rt = recent_turns(user_id=user_id, limit=3)
            if rt:
                recent_turns_payload = [
                    {"transcript": t.transcript, "reply": t.reply, "via": t.via}
                    for t in rt
                ]
                last = rt[-1]
                prior_turn_ctx = {
                    "transcript": last.transcript,
                    "reply": last.reply,
                    "via": last.via,
                }
        except Exception as exc:
            log_event("orchestrator.recent_turns_error", error=repr(exc))

        decision = await router().decide(
            transcript,
            agents_catalog=agents().describe(),
            prior_turn=prior_turn_ctx,
        )
        log_event(
            "router.decision",
            kind=decision.kind,
            agent=decision.agent,
            confidence=decision.confidence,
            reason=decision.reason,
        )

        target_name: str
        via: str
        if decision.kind == "direct" and decision.agent:
            target_name = decision.agent
            via = f"direct:{decision.agent}"
        elif decision.kind == "smalltalk":
            target_name = _SMALLTALK_AGENT
            via = "smalltalk"
        else:
            target_name = "supervisor"
            via = "supervisor"

        target = agents().get(target_name)
        if target is None:
            return await _terminate(
                turn, transcript, user_id,
                reply="I'm not set up to handle that yet.",
                status=AgentStatus.ERROR,
                error=f"no agent: {target_name}",
                via="router-fallback",
            )

        req = AgentRequest(
            turn_id=turn.turn_id,
            agent=target.name,
            goal=transcript,
            transcript=transcript,
            context={
                "user_id": user_id,
                "recent_turns": recent_turns_payload,
            },
        )
        resp: AgentResponse = await target.handle(req)

        # Retry-on-refusal: if a directly-routed specialist replies with a
        # "I can't / I don't have that" pattern, it usually means the
        # router picked the wrong agent — most often research refusing a
        # shopping query because it has no web tools on the fast path.
        # Re-dispatch through supervisor, which plans more carefully and
        # can delegate to the right specialist. Only retry once, and only
        # when we came in via a direct route (supervisor/smalltalk
        # retries would loop or double-charge the user for no gain).
        if via.startswith("direct:") and resp.status == AgentStatus.REFUSED:
            log_event(
                "orchestrator.refusal_retry",
                original_agent=target.name,
                reply=(resp.speak or "")[:200],
            )
            supervisor = agents().get("supervisor")
            if supervisor is not None:
                retry_req = AgentRequest(
                    turn_id=turn.turn_id,
                    agent=supervisor.name,
                    goal=transcript,
                    transcript=transcript,
                    context={
                        "user_id": user_id,
                        "recent_turns": recent_turns_payload,
                        "prior_refusal": {
                            "agent": target.name,
                            "reply": resp.speak,
                        },
                    },
                )
                retry_resp = await supervisor.handle(retry_req)
                # Only keep the retry if it produced something better
                # than another refusal — otherwise we'd just delay the
                # same "I can't" answer by one hop and burn tokens.
                if retry_resp.status == AgentStatus.DONE:
                    resp = retry_resp
                    target = supervisor
                    via = "supervisor-retry"
                    log_event(
                        "orchestrator.refusal_retry_success",
                        recovered_reply=(resp.speak or "")[:200],
                    )
                else:
                    log_event("orchestrator.refusal_retry_no_improvement")

        # Strip markdown emphasis (`**bold**`, `__emph__`) from the spoken
        # reply before it reaches TTS, the card auto-parser, or the session
        # log. Some agents let the planner leak markdown in despite prompt
        # hygiene — doing it once here keeps every downstream consumer clean.
        if resp.speak:
            resp.speak = strip_markdown(resp.speak)

        # Phase A: log-only modality classification. The decision is
        # observed but not yet enforced — downstream behavior is
        # unchanged. We run this before the confirmation-prompt
        # fallback so the event reflects the agent's raw output.
        try:
            decision = modality.classify(resp, transcript)
            log_event(
                "modality.decision",
                **modality.log_payload(decision, resp, transcript),
                via=via,
            )
        except Exception as exc:  # classifier must never break a turn
            log_event("modality.classify_error", error=repr(exc))

        # Visual card emission. Two sources, checked in order:
        #   1. Agent-set ui_payload — trusted, richer (images, urls).
        #   2. Auto-parsed list from resp.speak — zero agent changes,
        #      triggers whenever the reply has ≥ 2 bullets/numbers.
        # The card is *additive*: TTS still runs normally. If the user
        # likes cards, a follow-up change can silence TTS on VISUAL
        # turns. For now we just surface the panel.
        try:
            from mira.ui import cards as _cards
            card = _cards.coerce_payload(resp.ui_payload)
            if card is None and resp.speak:
                card = _cards.parse_list_reply(resp.speak)
            # Skip empty cards — an agent that sets ui_payload but
            # leaves rows empty, or any other shape that parsed into a
            # Card with nothing visible, would render as just a "Results"
            # header floating under the pill. That's worse than no card.
            if card is not None and card.rows:
                log_event(
                    "ui.card",
                    **card.to_dict(),
                    agent=target.name,
                    turn_id=turn.turn_id,
                )
        except Exception as exc:
            log_event("ui.card_error", error=repr(exc))

        reply = resp.speak
        if (
            resp.status == AgentStatus.NEED_CONFIRMATION
            and not reply
            and resp.confirmation
        ):
            reply = resp.confirmation.prompt

        # Persist pending confirmation so the next turn's "yes" can resume.
        if resp.status == AgentStatus.NEED_CONFIRMATION and resp.confirmation:
            set_pending(
                PendingConfirmation(
                    original_turn_id=turn.turn_id,
                    agent=target.name,
                    tool_call=resp.confirmation.action,
                    prompt=resp.confirmation.prompt,
                ),
                user_id=user_id,
            )

        # When the agent produced a live token stream, bypass the normal
        # reply pipeline: don't publish turn.completed yet (voice loop will
        # after the stream exhausts and it knows the final text), don't
        # record_turn, don't cache. The voice loop owns those side effects
        # for streaming turns so it can log the full text it actually spoke.
        if resp.speak_stream is not None:
            return TurnResult(
                turn_id=turn.turn_id,
                transcript=transcript,
                reply=None,
                status=resp.status,
                error=resp.error,
                via=via,
                reply_stream=resp.speak_stream,
                silent=resp.silent,
            )

        result = TurnResult(
            turn_id=turn.turn_id,
            transcript=transcript,
            reply=reply,
            status=resp.status,
            error=resp.error,
            via=via,
            silent=resp.silent,
        )
        await bus().publish(
            "turn.completed",
            turn_id=turn.turn_id,
            transcript=transcript,
            reply=reply or "",
            status=resp.status.value,
            via=via,
        )
        record_turn(
            turn_id=turn.turn_id,
            transcript=transcript,
            reply=reply or "",
            status=resp.status.value,
            via=via,
            user_id=user_id,
        )
        # Cache DONE replies only — reply_cache.put enforces this, but keep
        # the call site readable. NEED_CONFIRMATION and errors never cache.
        # Skip caching when a volatile tool ran this turn (live scores, news,
        # search): the 30s TTL can span a score change, which is exactly the
        # wrong class of thing to serve stale.
        if volatile_tool_hit():
            log_event("orchestrator.reply_cache_skip", reason="volatile_tool_hit")
        else:
            reply_cache.put(
                transcript,
                user_id=user_id,
                reply=reply or "",
                status=resp.status.value,
                via=via,
            )
        return result


async def _resume_confirmed(
    turn: Turn,
    transcript: str,
    pending: PendingConfirmation,
    user_id: str,
) -> TurnResult:
    """Dispatch the previously-approved tool and speak a short outcome.

    We intentionally do NOT re-enter the original agent's message history.
    Reconstructing it is expensive, and for side-effectful tools the user
    mostly wants confirmation that the action happened. Follow-up turns
    can ask for a richer narration.
    """
    call = pending.tool_call
    # Flip the confirm flag — dispatch path honors it, and the user has now
    # approved. We clear pending *before* dispatch so a failure mid-dispatch
    # doesn't leave a zombie.
    call.requires_confirmation = False
    clear_pending(user_id)

    log_event(
        "orchestrator.resume_confirmed",
        tool=call.tool,
        original_turn_id=pending.original_turn_id,
    )
    result = await registry().dispatch(call)

    if result.ok:
        spec = registry().get(call.tool)
        reply = spec.success_phrase if spec is not None else "Done."
        status = AgentStatus.DONE
        error = None
    else:
        reply = f"That didn't go through: {result.error or 'unknown error'}."
        status = AgentStatus.ERROR
        error = result.error

    await bus().publish(
        "turn.completed",
        turn_id=turn.turn_id,
        transcript=transcript,
        reply=reply,
        status=status.value,
        via="confirmation-resume",
    )
    record_turn(
        turn_id=turn.turn_id,
        transcript=transcript,
        reply=reply,
        status=status.value,
        via="confirmation-resume",
        user_id=user_id,
    )
    return TurnResult(
        turn_id=turn.turn_id,
        transcript=transcript,
        reply=reply,
        status=status,
        error=error,
        via="confirmation-resume",
    )


async def _terminate(
    turn: Turn,
    transcript: str,
    user_id: str,
    *,
    reply: str,
    status: AgentStatus,
    error: str | None,
    via: str,
) -> TurnResult:
    await bus().publish(
        "turn.completed",
        turn_id=turn.turn_id,
        transcript=transcript,
        reply=reply,
        status=status.value,
        via=via,
    )
    record_turn(
        turn_id=turn.turn_id,
        transcript=transcript,
        reply=reply,
        status=status.value,
        via=via,
        user_id=user_id,
    )
    return TurnResult(
        turn_id=turn.turn_id,
        transcript=transcript,
        reply=reply,
        status=status,
        error=error,
        via=via,
    )


async def _run_fast_path(
    turn: Turn,
    transcript: str,
    fm: "fast_path.FastMatch",
    user_id: str,
) -> TurnResult:
    """Dispatch a regex-matched deterministic tool and speak its outcome.

    No router, no specialist, no planner — just the tool call and a short
    spoken reply. Mirrors the shape of the normal path so the bus event,
    turn record, and TurnResult all look the same to downstream consumers.
    """
    log_event("orchestrator.fast_path", tool=fm.tool, args=fm.args)
    call = ToolCall(tool=fm.tool, args=fm.args, requires_confirmation=False)
    result = await registry().dispatch(call)

    if result.ok:
        reply = fm.speak(result.data)
        status = AgentStatus.DONE
        error = None
    else:
        reply = f"That didn't go through: {result.error or 'unknown error'}."
        status = AgentStatus.ERROR
        error = result.error

    via = "fast-path"
    await bus().publish(
        "turn.completed",
        turn_id=turn.turn_id,
        transcript=transcript,
        reply=reply,
        status=status.value,
        via=via,
    )
    record_turn(
        turn_id=turn.turn_id,
        transcript=transcript,
        reply=reply,
        status=status.value,
        via=via,
        user_id=user_id,
    )
    # Deliberately don't populate reply_cache for fast-path hits. time.now
    # must stay fresh, and for mutating tools (pause/volume/etc.) the
    # dispatch path already invalidates the cache via _is_mutating.
    return TurnResult(
        turn_id=turn.turn_id,
        transcript=transcript,
        reply=reply,
        status=status,
        error=error,
        via=via,
    )


