from __future__ import annotations

import asyncio
import re
import threading
from typing import AsyncIterator

import numpy as np


_HYPHEN_BETWEEN_NUMBERS = re.compile(r"(\d)\s*[-–]\s*(\d)")


def _normalize_for_tts(text: str) -> str:
    """Rewrite patterns that Cartesia spells out digit-by-digit.

    The big offender is hyphenated scores ("126-121" → "one two six to
    one two one"). Replacing the hyphen with " to " lets the voice read
    each number as a whole. We apply the same rule to en-dashes because
    LLMs sometimes emit those."""
    return _HYPHEN_BETWEEN_NUMBERS.sub(r"\1 to \2", text)

from mira.config.settings import get_settings
from mira.obs.logging import log_event
from mira.runtime.bus import bus
from mira.runtime.tracing import span
from mira.voice import summarizer, tts_cache


class TTS:
    """Cartesia Sonic-2 text-to-speech with streaming input + barge-in support.

    Design choices that matter:
      * `speak_stream(chunks)` accepts an async iterator of text deltas so we
        start synthesizing — and playing — on the first LLM token. This is
        the single largest perceived-latency win in the whole turn.
      * `cancel()` sets an internal event that the streaming loop polls; any
        in-flight playback stops within one audio buffer. Barge-in wires this
        up to `speech.started` on the bus.
      * Playback uses sounddevice at 24kHz. The Cartesia SDK gives us raw
        float32 PCM, so no codec conversion is needed on the hot path.
    """

    SAMPLE_RATE = 24000

    def __init__(self) -> None:
        self._settings = get_settings()
        self._client = None
        # threading.Event, not asyncio.Event — cancel() is called from the
        # AppKit/rumps thread (tray menu) and _on_speech_started fires on the
        # SpeechMonitor thread. asyncio.Event.set() isn't thread-safe: writes
        # from a non-loop thread can race the waiter list and silently drop
        # the cancel. The stream only polls is_set(), never awaits, so a
        # plain threading.Event is a safe drop-in with the same API.
        self._cancel_event = threading.Event()
        self._wire_barge_in()

    def _wire_barge_in(self) -> None:
        def _on_speech_started(_topic: str, _payload: dict) -> None:
            # If the user starts speaking while we're talking, cut the TTS.
            if not self._cancel_event.is_set():
                self._cancel_event.set()
                log_event("tts.barge_in")

        bus().subscribe("speech.started", _on_speech_started)

    def _get_client(self):
        if self._client is None:
            if not self._settings.cartesia_api_key:
                raise RuntimeError("CARTESIA_API_KEY is not set")
            if not self._settings.cartesia_voice:
                raise RuntimeError("CARTESIA_VOICE is not set")
            from cartesia import Cartesia

            self._client = Cartesia(api_key=self._settings.cartesia_api_key)
        return self._client

    def cancel(self) -> None:
        self._cancel_event.set()
        bus().publish_nowait("tts.cancelled")

    async def speak(self, text: str) -> None:
        """Single-shot synthesis for short, fixed utterances (errors, confirmations).

        Three things happen before we hand off to streaming synthesis:
          1. If the text is long enough, route through the Haiku
             compressor. It returns the original on any failure/timeout,
             so TTS never blocks on this step.
          2. Normalize (hyphen→" to ", etc.) so cache keys stay
             consistent across whatever variants the agents produce.
          3. Check the disk cache; on hit, play the stored PCM directly
             and skip Cartesia entirely. On miss, synthesize, play, and
             (if the synth produced anything) write the result to cache
             for next time — but only for short utterances, since the
             cache is aimed at repeated confirmations, not novel
             multi-sentence replies.
        """
        if not text.strip():
            return
        spoken = await summarizer.compress(text)
        normalized = _normalize_for_tts(spoken)
        if await self._try_speak_cached(normalized):
            return
        # Miss: synthesize+play+cache in one pass for short utterances,
        # otherwise fall back to the streaming path (no cache write —
        # long novel replies almost never repeat, and the disk churn
        # isn't worth it).
        if _is_cacheable_length(normalized):
            await self._speak_and_cache(normalized)
        else:
            await self.speak_stream(_single(normalized))

    async def _try_speak_cached(self, text: str) -> bool:
        """Play from cache if present. Returns True on hit (audio
        played), False on miss (caller must synthesize).

        Mirrors the streaming path's bus events (`tts.started`,
        `tts.ended`) and barge-in support so the rest of the system
        can't tell the difference between a cache hit and a fresh
        synth."""
        voice_id = self._settings.cartesia_voice or ""
        model = self._settings.cartesia_tts_model or ""
        pcm = tts_cache.get(text, voice_id=voice_id, model=model)
        if pcm is None:
            return False

        self._cancel_event.clear()
        bus().publish_nowait("tts.started")

        import sounddevice as sd

        stream_out = sd.OutputStream(
            samplerate=self.SAMPLE_RATE, channels=1, dtype="float32"
        )
        stream_out.start()
        try:
            # Chunked write so barge-in can interrupt mid-phrase. 2048
            # frames ≈ 85ms at 24kHz — well under human reaction time.
            chunk_size = 2048
            for i in range(0, pcm.size, chunk_size):
                if self._cancel_event.is_set():
                    break
                await asyncio.to_thread(stream_out.write, pcm[i : i + chunk_size])
        finally:
            stream_out.stop()
            stream_out.close()
            bus().publish_nowait(
                "tts.ended", cancelled=self._cancel_event.is_set()
            )
        return True

    async def _speak_and_cache(self, text: str) -> None:
        """Synthesize `text` in one shot, play it, and write the PCM to
        cache. Used for short cacheable utterances on first encounter.

        Uses the same Cartesia call as the streaming path but without
        the per-flush pipelining — for a 2–8 word phrase the whole
        synth is one network round-trip, so streaming buys nothing."""
        self._cancel_event.clear()
        bus().publish_nowait("tts.started")

        client = self._get_client()
        voice_id = self._settings.cartesia_voice or ""
        model = self._settings.cartesia_tts_model or ""

        def _synth() -> list[np.ndarray]:
            out: list[np.ndarray] = []
            try:
                for audio_chunk in client.tts.bytes(
                    model_id=model,
                    transcript=text,
                    voice={"id": voice_id},
                    output_format={
                        "container": "raw",
                        "encoding": "pcm_f32le",
                        "sample_rate": TTS.SAMPLE_RATE,
                    },
                ):
                    out.append(np.frombuffer(audio_chunk, dtype=np.float32))
            except Exception as exc:
                log_event("tts.synth_error", error=repr(exc))
            return out

        import sounddevice as sd

        stream_out = sd.OutputStream(
            samplerate=self.SAMPLE_RATE, channels=1, dtype="float32"
        )
        stream_out.start()

        with span("tts.oneshot", model=model):
            try:
                chunks = await asyncio.to_thread(_synth)
                if not chunks:
                    return
                full = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
                # Play first — cache write is best-effort and must never
                # delay the user hearing the reply.
                chunk_size = 2048
                for i in range(0, full.size, chunk_size):
                    if self._cancel_event.is_set():
                        break
                    await asyncio.to_thread(stream_out.write, full[i : i + chunk_size])
                # Only cache if we played the whole thing (partial PCM
                # from a barge-in would poison the cache).
                if not self._cancel_event.is_set() and full.size > 0:
                    tts_cache.put(text, full, voice_id=voice_id, model=model)
            finally:
                stream_out.stop()
                stream_out.close()
                bus().publish_nowait(
                    "tts.ended", cancelled=self._cancel_event.is_set()
                )

    async def speak_stream(self, chunks: AsyncIterator[str]) -> None:
        """Stream text → audio → speakers with pipelined synthesis.

        Producer: reads LLM deltas, flushes on ~40-char or sentence
        boundary, and *immediately* spawns a thread to synthesize each
        piece. Consumer: awaits synth tasks in order and writes PCM to
        the output stream. Playback of piece N overlaps synthesis of
        piece N+1..N+k, so the gap between sentences is a single audio
        buffer instead of a full Cartesia round-trip (~300-700ms).

        Cancel is polled at every boundary — flush loop, consumer loop,
        and per-PCM-buffer — so barge-in stops within one audio buffer
        (~10-20ms). In-flight synth tasks drain naturally; their output
        is dropped by the consumer's cancel check."""
        self._cancel_event.clear()
        bus().publish_nowait("tts.started")

        client = self._get_client()
        voice_id = self._settings.cartesia_voice
        model = self._settings.cartesia_tts_model

        import sounddevice as sd

        stream_out = sd.OutputStream(
            samplerate=self.SAMPLE_RATE, channels=1, dtype="float32"
        )
        stream_out.start()

        flush_at = 40

        def _synthesize(piece: str) -> list[np.ndarray]:
            result: list[np.ndarray] = []
            try:
                for audio_chunk in client.tts.bytes(
                    model_id=model,
                    transcript=piece,
                    voice={"id": voice_id},
                    output_format={
                        "container": "raw",
                        "encoding": "pcm_f32le",
                        "sample_rate": TTS.SAMPLE_RATE,
                    },
                ):
                    result.append(np.frombuffer(audio_chunk, dtype=np.float32))
            except Exception as exc:
                log_event("tts.synth_error", error=repr(exc))
            return result

        # Ordered queue of in-flight synth tasks. Unbounded because the
        # producer (LLM token rate) is always slower than the consumer
        # (Cartesia synth + speaker write), so backpressure isn't needed
        # in practice. Sentinel None marks end-of-stream.
        synth_queue: asyncio.Queue[asyncio.Task[list[np.ndarray]] | None] = asyncio.Queue()

        async def _consume() -> None:
            while True:
                task = await synth_queue.get()
                if task is None:
                    return
                if self._cancel_event.is_set():
                    # Drop this task's audio but keep draining the queue so
                    # the producer can still reach the sentinel and finish.
                    try:
                        await task
                    except Exception:
                        pass
                    continue
                try:
                    audio_chunks = await task
                except Exception as exc:
                    log_event("tts.synth_task_error", error=repr(exc))
                    continue
                for pcm in audio_chunks:
                    if self._cancel_event.is_set():
                        break
                    # Speaker write is blocking (~buffer_size / sample_rate);
                    # push it to a thread so the consumer coroutine can keep
                    # awaiting the next synth task concurrently.
                    await asyncio.to_thread(stream_out.write, pcm)

        consumer = asyncio.create_task(_consume())

        def _submit(piece: str) -> None:
            piece = _normalize_for_tts(piece)
            task = asyncio.create_task(asyncio.to_thread(_synthesize, piece))
            synth_queue.put_nowait(task)

        with span("tts.stream", model=model):
            buffer = ""
            try:
                async for delta in chunks:
                    if self._cancel_event.is_set():
                        break
                    buffer += delta
                    if len(buffer) >= flush_at or any(p in delta for p in ".!?\n"):
                        piece, buffer = buffer, ""
                        _submit(piece)
                if buffer and not self._cancel_event.is_set():
                    _submit(buffer)
            finally:
                synth_queue.put_nowait(None)
                try:
                    await consumer
                except Exception as exc:
                    log_event("tts.consumer_error", error=repr(exc))
                stream_out.stop()
                stream_out.close()
                bus().publish_nowait(
                    "tts.ended", cancelled=self._cancel_event.is_set()
                )


async def _single(text: str) -> AsyncIterator[str]:
    yield text


# Only cache short utterances. Long novel replies (research answers,
# paragraph explanations) rarely repeat, so caching them just burns disk
# and drags the LRU sweep. 8 words covers every confirmation, error, and
# fast-path reply in the codebase today.
_CACHE_MAX_WORDS = 8


def _is_cacheable_length(text: str) -> bool:
    return len(text.split()) <= _CACHE_MAX_WORDS


_tts: TTS | None = None


def tts() -> TTS:
    global _tts
    if _tts is None:
        _tts = TTS()
    return _tts
