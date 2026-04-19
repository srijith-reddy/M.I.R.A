"""Disk-backed cache of Cartesia-synthesized PCM audio, keyed by (voice,
model, text). MIRA says a small set of phrases constantly — "Done.",
"Sent.", "Okay, cancelled.", "Playing." — and paying Cartesia to
re-synthesize them every turn is pure waste.

Hit path: lookup on sha256 of (voice_id, model, normalized_text), read
the raw float32 PCM bytes off disk, return as a single np.ndarray. The
caller writes it straight to sounddevice — no synthesis, no network.

Miss path: caller synthesizes normally, then calls `put()` with the
final PCM. We keep per-entry latency under ~5ms by storing raw float32
(no encoding) and indexing entirely by filename — the filename IS the
key, so no separate index file to keep consistent.

LRU eviction: capped at ~200MB total by atime. Cheap to run at startup.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import numpy as np

from mira.config.paths import paths
from mira.obs.logging import log_event

# Keep this list short and high-confidence. Every entry here gets a
# Cartesia call at first startup (one-time cost ~$0.01 for the batch),
# and from then on every matching utterance is free. Phrases MIRA emits
# from fixed code paths (success_phrase on tools, fast-path speak,
# confirmation-resume, smalltalk stock replies) belong here.
#
# Punctuation matters — the cache key includes it. Match what the code
# actually emits, not what "feels right".
STOCK_PHRASES: tuple[str, ...] = (
    "Done.",
    "Okay.",
    "Okay, cancelled.",
    "Got it.",
    "Sure.",
    "Sent.",
    "Saved.",
    "Playing.",
    "Paused.",
    "Resumed.",
    "Stopped.",
    "Sorry, I didn't catch that.",
    "That didn't go through.",
    "Nothing playing.",
    "I'm not set up to handle that yet.",
    "I got stuck on that — try rephrasing?",
    "I ran out of steps. Want to narrow it down?",
    "Yes.",
    "No.",
    "Ready.",
    "One moment.",
)

_CACHE_SUBDIR = "tts"
_MAX_BYTES = 200 * 1024 * 1024  # 200MB
_SAMPLE_RATE = 24000  # must match TTS.SAMPLE_RATE


def _cache_dir() -> Path:
    d = paths.cache_dir / _CACHE_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _normalize(text: str) -> str:
    # Match on trimmed exact string. Case matters for TTS prosody, so we
    # don't lower(). Whitespace collapse catches cosmetic variance.
    return " ".join(text.split())


def key_for(text: str, *, voice_id: str, model: str) -> str:
    normalized = _normalize(text)
    h = hashlib.sha256(f"{voice_id}|{model}|{normalized}".encode("utf-8")).hexdigest()
    return h[:32]


def _path_for(key: str) -> Path:
    return _cache_dir() / f"{key}.f32"


def get(text: str, *, voice_id: str, model: str) -> np.ndarray | None:
    """Return the cached PCM for `text`, or None on miss.

    Updates the file's atime so LRU eviction sees it as recently used.
    """
    k = key_for(text, voice_id=voice_id, model=model)
    p = _path_for(k)
    if not p.exists():
        return None
    try:
        # Bump atime so this entry survives the next eviction pass. We
        # use os.utime rather than reading + writing — faster and doesn't
        # change mtime, which we'd want to preserve if we ever add
        # version-based invalidation.
        now = time.time()
        os.utime(p, (now, p.stat().st_mtime))
        data = np.fromfile(str(p), dtype=np.float32)
        if data.size == 0:
            return None
        log_event("tts_cache.hit", key=k, samples=int(data.size))
        return data
    except OSError as exc:
        log_event("tts_cache.read_error", key=k, error=repr(exc))
        return None


def put(text: str, pcm: np.ndarray, *, voice_id: str, model: str) -> None:
    """Persist PCM float32 for (text, voice, model). No-op on failure —
    caching is always best-effort; a disk-full or permission error must
    not break playback."""
    if pcm is None or pcm.size == 0:
        return
    if pcm.dtype != np.float32:
        pcm = pcm.astype(np.float32)
    k = key_for(text, voice_id=voice_id, model=model)
    p = _path_for(k)
    try:
        pcm.tofile(str(p))
        log_event("tts_cache.put", key=k, samples=int(pcm.size))
    except OSError as exc:
        log_event("tts_cache.write_error", key=k, error=repr(exc))


def sweep(max_bytes: int = _MAX_BYTES) -> None:
    """Evict least-recently-used entries until total size ≤ max_bytes.

    Cheap enough to run on startup; O(n log n) on the number of cached
    files. In practice n is under a few thousand for a well-populated
    cache, so this is sub-millisecond."""
    d = _cache_dir()
    try:
        entries = [(p.stat().st_atime, p.stat().st_size, p) for p in d.glob("*.f32")]
    except OSError:
        return
    total = sum(sz for _, sz, _ in entries)
    if total <= max_bytes:
        return
    entries.sort()  # oldest atime first
    removed = 0
    for _, size, path in entries:
        if total <= max_bytes:
            break
        try:
            path.unlink()
            total -= size
            removed += 1
        except OSError:
            continue
    if removed:
        log_event("tts_cache.sweep", removed=removed, remaining_bytes=total)


def prewarm_if_empty(*, voice_id: str, model: str, synthesize) -> int:
    """Populate STOCK_PHRASES on first run. `synthesize(text) -> np.ndarray`
    is provided by the caller so this module stays Cartesia-free and
    importable in tests. Returns count of phrases newly written.

    Skips any phrase that's already cached — running this every startup
    is safe and idempotent."""
    written = 0
    for phrase in STOCK_PHRASES:
        if get(phrase, voice_id=voice_id, model=model) is not None:
            continue
        try:
            pcm = synthesize(phrase)
        except Exception as exc:
            log_event("tts_cache.prewarm_synth_error", phrase=phrase, error=repr(exc))
            continue
        if pcm is None or getattr(pcm, "size", 0) == 0:
            continue
        put(phrase, pcm, voice_id=voice_id, model=model)
        written += 1
    if written:
        log_event("tts_cache.prewarmed", count=written)
    return written


def sample_rate() -> int:
    return _SAMPLE_RATE
