from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass

# 30s TTL. Short enough that "what time is it" doesn't drift noticeably,
# long enough to cover the common case: user asks the same thing twice
# because they didn't hear / MIRA got interrupted / they're double-checking.
_TTL_SECS = 30.0

# Cap entries so a pathological session can't grow the cache forever.
# Opportunistic eviction on every `put` when we cross the threshold.
_MAX_ENTRIES = 128

# Strip leading filler before hashing so "uh what time is it" and
# "what time is it" hit the same row. List is intentionally short —
# over-aggressive normalization risks collapsing distinct utterances
# ("play despacito" vs "stop despacito" must stay distinct).
_LEADING_FILLER = re.compile(
    r"^(um+|uh+|hmm+|hey mira|okay mira|mira|please|so|like|well|just)\b[,\s]*",
    re.IGNORECASE,
)
_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class CachedReply:
    reply: str
    status: str          # AgentStatus.value
    via: str             # original routing path, prefixed with "cached:"
    expires_at: float


_store: dict[str, CachedReply] = {}


def _normalize(transcript: str) -> str:
    """Lowercase, strip filler + punctuation, collapse whitespace.

    Deliberately does NOT do aggressive lemmatization — we want "what time
    is it" and "what was the time" to be different keys. The only collapses
    are leading disfluencies and punctuation/whitespace noise that STT
    varies on between runs."""
    s = transcript.strip().lower()
    s = _LEADING_FILLER.sub("", s)
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s


def _key(transcript: str, user_id: str) -> str:
    norm = _normalize(transcript)
    if not norm:
        return ""
    h = hashlib.sha256(f"{user_id}\x00{norm}".encode("utf-8")).hexdigest()
    return h[:32]


def get(transcript: str, *, user_id: str) -> CachedReply | None:
    k = _key(transcript, user_id)
    if not k:
        return None
    entry = _store.get(k)
    if entry is None:
        return None
    if entry.expires_at < time.time():
        _store.pop(k, None)
        return None
    return entry


def put(
    transcript: str,
    *,
    user_id: str,
    reply: str,
    status: str,
    via: str,
) -> None:
    """Store a reply. No-ops on empty input / empty reply / non-DONE status —
    we never want to cache an error message or a confirmation prompt, since
    the conditions that produced them are transient."""
    k = _key(transcript, user_id)
    if not k or not reply.strip():
        return
    if status != "done":
        return

    _store[k] = CachedReply(
        reply=reply,
        status=status,
        via=f"cached:{via}",
        expires_at=time.time() + _TTL_SECS,
    )

    if len(_store) > _MAX_ENTRIES:
        now = time.time()
        for cached_k, cached_v in list(_store.items()):
            if cached_v.expires_at < now:
                _store.pop(cached_k, None)
        # Still too big? Drop the oldest by expiration.
        if len(_store) > _MAX_ENTRIES:
            oldest = sorted(_store.items(), key=lambda kv: kv[1].expires_at)
            for cached_k, _ in oldest[: len(_store) - _MAX_ENTRIES]:
                _store.pop(cached_k, None)


def invalidate(user_id: str | None = None) -> int:
    """Drop all cached entries, optionally only for one user. Returns count
    dropped. Called from places that mutate state a cached reply might have
    referenced (reminder created, profile changed, etc)."""
    if user_id is None:
        n = len(_store)
        _store.clear()
        return n
    prefix_probe = f"{user_id}\x00"
    # Keys are hashes, so we can't filter by user without recomputing.
    # Practical path: clear everything. user_id scoping is only meaningful
    # for lookup (we key by it), not for selective invalidation here.
    n = len(_store)
    _store.clear()
    return n
