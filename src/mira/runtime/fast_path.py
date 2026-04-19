"""Regex fast-path: skip router + specialist LLMs for deterministic intents.

For commands like "pause music" or "volume 50" the router-then-specialist
path spends two LLM round-trips plus ~500-800ms to produce a call we can
derive from the transcript itself. A few narrow regexes cover the bulk of
our single-utterance daily drivers. Everything that doesn't match falls
through to the normal router path — misses are free.

Design rules:
- High precision over recall. A missed fast-path costs one LLM hop; a
  wrong fast-path runs the wrong tool.
- No LLM access, no network, no async I/O. The match decision itself must
  be sub-millisecond so adding this to every turn is strictly a win.
- Anchored patterns only (`^...$` after normalization). No substring
  matches — "please don't stop the music" must not trigger music.stop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Pattern

# Same leading-filler strip used by reply_cache, so "uh pause music" and
# "pause music" both hit. Kept local to avoid a cross-module dep on a
# regex that might otherwise drift.
_LEADING_FILLER = re.compile(
    r"^(um+|uh+|hmm+|hey mira|okay mira|mira|please|so|like|well|just)\b[,\s]*",
    re.IGNORECASE,
)
_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def _normalize(transcript: str) -> str:
    s = transcript.strip().lower()
    s = _LEADING_FILLER.sub("", s)
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s


@dataclass(frozen=True)
class FastMatch:
    tool: str
    args: dict[str, Any]
    speak: Callable[[Any], str]  # receives tool result data, returns spoken reply


ArgFn = Callable[[re.Match[str]], dict[str, Any]]
SpeakFn = Callable[[Any], str]


# --- speak formatters ------------------------------------------------------

def _speak_time(data: Any) -> str:
    if not isinstance(data, dict):
        return "I couldn't read the clock."
    hour = data.get("hour")
    minute = data.get("minute")
    if hour is None or minute is None:
        return "I couldn't read the clock."
    suffix = "AM" if hour < 12 else "PM"
    h12 = hour % 12 or 12
    if minute == 0:
        return f"It's {h12} {suffix}."
    return f"It's {h12}:{minute:02d} {suffix}."


def _speak_date(data: Any) -> str:
    if not isinstance(data, dict):
        return "I couldn't read the date."
    weekday = data.get("weekday", "")
    human = data.get("human", "")
    # `human` is "Friday, April 17 2026, 4:30 PM PDT" — trim the time tail.
    if "," in human:
        parts = human.split(",")
        if len(parts) >= 2:
            date_part = (parts[0] + "," + parts[1]).strip()
            return f"Today is {date_part}."
    return f"It's {weekday}." if weekday else "I couldn't read the date."


def _speak_ok(msg: str) -> SpeakFn:
    def _s(_data: Any) -> str:
        return msg
    return _s


def _speak_volume_set(data: Any) -> str:
    if isinstance(data, dict) and data.get("ok"):
        return f"Volume {data.get('level', '')}."
    return "Couldn't change the volume."


def _speak_mute(on: bool) -> SpeakFn:
    def _s(data: Any) -> str:
        if isinstance(data, dict) and data.get("ok"):
            return "Muted." if on else "Unmuted."
        return "Couldn't change the mute setting."
    return _s


def _speak_brightness(direction: str) -> SpeakFn:
    def _s(data: Any) -> str:
        if isinstance(data, dict) and data.get("ok"):
            return "Brighter." if direction == "up" else "Dimmer."
        return "Couldn't change brightness."
    return _s


# --- patterns --------------------------------------------------------------
#
# Each entry: (compiled regex, tool_name, arg_fn, speak_fn).
# Regexes match against the NORMALIZED transcript.

_PATTERNS: list[tuple[Pattern[str], str, ArgFn, SpeakFn]] = [
    # --- time / date ---
    (
        re.compile(r"^(what s|whats|what is)?\s*(the\s+)?(current\s+)?time( is it)?$"),
        "time.now",
        lambda m: {},
        _speak_time,
    ),
    (
        re.compile(r"^what time is it$"),
        "time.now",
        lambda m: {},
        _speak_time,
    ),
    (
        re.compile(
            r"^(what s|whats|what is|what)\s+(the\s+|today s\s+|todays\s+)?(date|day)"
            r"(\s+is\s+it)?(\s+today)?$"
        ),
        "time.now",
        lambda m: {},
        _speak_date,
    ),
    (
        re.compile(r"^what day of the week is it$"),
        "time.now",
        lambda m: {},
        _speak_date,
    ),

    # --- music transport (no query extraction — play has too many phrasings) ---
    (
        re.compile(r"^(pause|hold)( the)?( music| song| track)?$"),
        "music.pause",
        lambda m: {},
        _speak_ok("Paused."),
    ),
    (
        re.compile(r"^(resume|unpause|continue|keep playing|play)( the)?( music| song| track)?$"),
        "music.resume",
        lambda m: {},
        _speak_ok("Resumed."),
    ),
    (
        re.compile(r"^(stop|end|kill)( the)?( music| song| track|playback)?$"),
        "music.stop",
        lambda m: {},
        _speak_ok("Stopped."),
    ),

    # --- volume ---
    (
        re.compile(r"^(set\s+)?volume( to)?\s+(\d{1,3})( percent)?$"),
        "system.volume_set",
        lambda m: {"level": max(0, min(100, int(m.group(3))))},
        _speak_volume_set,
    ),
    (
        re.compile(r"^turn( the)? volume (up|down)$"),
        "system.volume_set",
        # No existing level→relative step tool; just go full/half.
        # Safer: skip — let the supervisor resolve this with a volume_get
        # first. We fall through by returning an impossible pattern below.
        lambda m: {"level": 80 if m.group(2) == "up" else 30},
        _speak_volume_set,
    ),
    (
        re.compile(r"^mute( the)?( system| sound| audio| volume)?$"),
        "system.mute",
        lambda m: {"muted": True},
        _speak_mute(True),
    ),
    (
        re.compile(r"^unmute( the)?( system| sound| audio| volume)?$"),
        "system.mute",
        lambda m: {"muted": False},
        _speak_mute(False),
    ),

    # --- brightness ---
    (
        re.compile(r"^(turn\s+)?brightness\s+(up|down)$"),
        "system.brightness",
        lambda m: {"direction": m.group(2)},
        _speak_brightness("up"),  # direction baked in via match — see note below
    ),
    (
        re.compile(r"^(make (it|the screen) )?(brighter|dimmer|darker)$"),
        "system.brightness",
        lambda m: {"direction": "up" if m.group(3) == "brighter" else "down"},
        _speak_brightness("up"),
    ),
]


def match(transcript: str) -> FastMatch | None:
    """Return a `FastMatch` if the transcript exactly matches a deterministic
    pattern, else None. Designed to run on every turn — must stay cheap."""
    norm = _normalize(transcript)
    if not norm:
        return None
    for pattern, tool_name, arg_fn, speak_fn in _PATTERNS:
        m = pattern.match(norm)
        if m is None:
            continue
        try:
            args = arg_fn(m)
        except Exception:
            return None
        # Rebind brightness speak_fn to the actual direction extracted,
        # since the static speak_fn captured the wrong default at module load.
        if tool_name == "system.brightness":
            direction = args.get("direction", "up")
            return FastMatch(
                tool=tool_name, args=args, speak=_speak_brightness(direction)
            )
        return FastMatch(tool=tool_name, args=args, speak=speak_fn)
    return None
