"""Deterministic modality classifier.

Given an `AgentResponse` and the user's transcript, decide whether the
reply should be delivered as VOICE, HYBRID (short voice + visual),
VISUAL, or SILENT. Purely rule-based — no LLM calls, microsecond-cheap.

**Phase A note:** this module runs in log-only mode. The orchestrator
calls `classify()` after every turn and emits a `modality.decision`
event, but does NOT yet route on the result. Once we have a week of
decisions logged we'll turn on actual routing. Until then, changing
outputs here only affects the log line — safe to iterate on."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from mira.runtime.schemas import AgentResponse, AgentStatus


class ModalityClass(str, Enum):
    VOICE = "voice"          # short spoken reply, no card
    HYBRID = "hybrid"        # ≤12-word voice preamble + card
    VISUAL = "visual"        # card only, no TTS
    SILENT = "silent"        # no TTS, no card; tool side effect is the reply


@dataclass(frozen=True)
class ModalityDecision:
    modality: ModalityClass
    reason: str                     # short tag for logging/analytics
    speak_text: str | None          # what TTS should say (may be trimmed/empty)
    has_card: bool                  # whether a ui_payload was present


# Signals that the user explicitly asked for a visual reply. Matched on
# word boundaries so "showcase" doesn't trip "show". Keep the list tight
# — over-matching here silences voice for things the user wanted to hear.
_VISUAL_INTENT = re.compile(
    r"\b(show|list|display|pull up|open|compare|comparison|side by side|diff|"
    r"table|chart|graph|preview)\b",
    re.IGNORECASE,
)

# Signals the user explicitly wants audio. These *win* against everything
# except SILENT — if a tool is silent-by-contract, we respect that.
_VOICE_INTENT = re.compile(
    r"\b(tell me|say|read|speak|aloud|out loud|narrate|explain|summarize)\b",
    re.IGNORECASE,
)

# If the reply text itself looks like a list/table, it's a card, not a
# monologue. We look for multiple enumerated lines, markdown bullets, or
# structured separators. Single-line replies with a comma are fine.
_LIST_LIKE = re.compile(
    r"(^\s*[-*•]\s+.+(\n\s*[-*•]\s+.+){1,})"           # bullets, ≥ 2
    r"|(^\s*\d+[.)]\s+.+(\n\s*\d+[.)]\s+.+){1,})"      # numbered, ≥ 2
    r"|(\|.+\|\s*\n.+\|)"                               # markdown table
    ,
    re.MULTILINE,
)


_HYBRID_PREAMBLE_MAX_WORDS = 12
_VOICE_ONLY_MAX_WORDS = 12
_COMPRESS_THRESHOLD_WORDS = 40


def _word_count(text: str | None) -> int:
    return len(text.split()) if text else 0


def _looks_like_list(text: str) -> bool:
    return bool(_LIST_LIKE.search(text))


def _trim_to_preamble(text: str, max_words: int = _HYBRID_PREAMBLE_MAX_WORDS) -> str:
    """Take the first sentence, or the first `max_words` tokens.

    Used when we classify a reply as HYBRID but the agent's `speak` is
    too long — we keep the preamble and drop the rest (the rest is
    presumably on the card). Deterministic; no LLM.
    """
    if not text:
        return ""
    # First sentence, if it fits.
    m = re.match(r"([^.!?\n]+[.!?])", text.strip())
    if m:
        first = m.group(1).strip()
        if _word_count(first) <= max_words:
            return first
    # Otherwise first N words + ellipsis.
    words = text.strip().split()
    return " ".join(words[:max_words]).rstrip(",;:") + "…"


def classify(
    resp: AgentResponse,
    transcript: str,
) -> ModalityDecision:
    """Return the modality this reply *should* use.

    Phase A: caller logs the decision but does NOT act on `speak_text`
    or `has_card` — behavior is unchanged downstream. This keeps the
    classifier safe to tune while we collect data."""
    speak = resp.speak or ""
    card = resp.ui_payload
    wc = _word_count(speak)
    hint = resp.modality_hint
    transcript_visual = bool(_VISUAL_INTENT.search(transcript or ""))
    transcript_voice = bool(_VOICE_INTENT.search(transcript or ""))

    # 1. Hard rules that short-circuit everything else.
    if resp.silent:
        return ModalityDecision(ModalityClass.SILENT, "agent_silent_flag", None, False)

    if resp.status == AgentStatus.NEED_CONFIRMATION:
        # Confirmations are consequential and must be heard. Never
        # silently gated.
        return ModalityDecision(
            ModalityClass.VOICE,
            "confirmation_required",
            speak or None,
            False,
        )

    if resp.status == AgentStatus.ERROR:
        return ModalityDecision(
            ModalityClass.VOICE,
            "error_status",
            speak or "That didn't go through.",
            False,
        )

    # 2. Explicit agent hint (trusted but not absolute).
    if hint == "silent":
        return ModalityDecision(ModalityClass.SILENT, "agent_hint_silent", None, False)
    if hint == "visual" and not transcript_voice:
        return ModalityDecision(
            ModalityClass.VISUAL, "agent_hint_visual", None, bool(card)
        )
    if hint == "hybrid" and card is not None:
        return ModalityDecision(
            ModalityClass.HYBRID,
            "agent_hint_hybrid",
            _trim_to_preamble(speak) if wc > _HYBRID_PREAMBLE_MAX_WORDS else (speak or None),
            True,
        )

    # 3. User said "show me" / "list" / "compare" → visual unless they
    #    also said "tell me".
    if transcript_visual and not transcript_voice:
        if card is not None:
            return ModalityDecision(ModalityClass.VISUAL, "user_visual_intent", None, True)
        # No card available but user asked visual → downgrade to hybrid
        # so they at least get a short spoken answer.
        return ModalityDecision(
            ModalityClass.HYBRID,
            "user_visual_intent_no_card",
            _trim_to_preamble(speak) if wc > _HYBRID_PREAMBLE_MAX_WORDS else (speak or None),
            False,
        )

    # 4. Card present.
    if card is not None:
        if not speak:
            return ModalityDecision(ModalityClass.VISUAL, "card_only", None, True)
        if wc <= _HYBRID_PREAMBLE_MAX_WORDS:
            return ModalityDecision(ModalityClass.HYBRID, "card_with_short_speak", speak, True)
        # Speak is long but card carries detail — trim to preamble.
        return ModalityDecision(
            ModalityClass.HYBRID,
            "card_with_trimmed_speak",
            _trim_to_preamble(speak),
            True,
        )

    # 5. No card, voice only. Decide whether it should be compressed or
    #    promoted to visual (future — no card types yet).
    if not speak:
        # Empty reply, no card — nothing to say, but also nothing to
        # show. Treat as SILENT; something upstream is likely broken,
        # but the right behavior is still "say nothing".
        return ModalityDecision(ModalityClass.SILENT, "empty_reply", None, False)

    if _looks_like_list(speak):
        # This is the biggest TTS offender: a multi-bullet reply read
        # aloud. Flag it as visual even though we can't render a card
        # yet — the log will surface how often this happens.
        return ModalityDecision(
            ModalityClass.VISUAL, "reply_is_list_like", None, False
        )

    if wc >= _COMPRESS_THRESHOLD_WORDS:
        # Long monologue: mark for compression. Caller (Phase B) will
        # pass this through the summarizer before TTS.
        return ModalityDecision(
            ModalityClass.VOICE, "voice_long_compress", speak, False
        )

    return ModalityDecision(ModalityClass.VOICE, "voice_short", speak, False)


def log_payload(decision: ModalityDecision, resp: AgentResponse, transcript: str) -> dict:
    """Flat dict for `log_event("modality.decision", **log_payload(...))`.

    Kept separate so the event shape is stable across code paths; the
    dashboard indexes on these keys."""
    return {
        "modality": decision.modality.value,
        "reason": decision.reason,
        "has_card": decision.has_card,
        "agent": resp.agent,
        "status": resp.status.value,
        "speak_words": _word_count(resp.speak),
        "trimmed_words": _word_count(decision.speak_text),
        "hint": resp.modality_hint,
        "transcript_words": _word_count(transcript),
    }


ModalityLiteral = Literal["voice", "hybrid", "visual", "silent"]
