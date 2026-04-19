from __future__ import annotations

import asyncio
import io
import time
from dataclasses import dataclass
from typing import Literal

import numpy as np

from mira.config.settings import get_settings
from mira.obs.logging import log_event
from mira.runtime.tracing import span

Provider = Literal["deepgram", "cartesia"]


@dataclass(frozen=True)
class Transcript:
    text: str
    provider: Provider
    latency_ms: int
    confidence: float | None = None


def _pcm_to_wav(pcm: np.ndarray, sample_rate: int) -> bytes:
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, pcm, sample_rate, subtype="PCM_16", format="WAV")
    return buf.getvalue()


class STT:
    """Speech-to-text with Deepgram Nova-3 primary, Cartesia Ink-Whisper fallback.

    Why two providers: Deepgram's streaming endpoint is ~150–200ms faster than
    batch providers and has the lowest word-error-rate on conversational
    English of any public API as of this writing. Cartesia is the fallback
    because we're already paying for TTS there and it's a single-line flip
    if Deepgram is down.

    The batch `transcribe()` used in the hot path takes a finished PCM buffer.
    Streaming STT (sockets, interim results) lives in a future revision —
    with STOP_MS=700 and Deepgram batch latency <400ms, the difference is
    swamped by STT→planner handoff anyway.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._deepgram = None
        self._cartesia = None

    def _deepgram_client(self):
        if self._deepgram is None:
            if not self._settings.deepgram_api_key:
                raise RuntimeError("DEEPGRAM_API_KEY is not set")
            from deepgram import DeepgramClient

            self._deepgram = DeepgramClient(self._settings.deepgram_api_key)
        return self._deepgram

    def _cartesia_client(self):
        if self._cartesia is None:
            if not self._settings.cartesia_api_key:
                raise RuntimeError("CARTESIA_API_KEY is not set")
            from cartesia import Cartesia

            self._cartesia = Cartesia(api_key=self._settings.cartesia_api_key)
        return self._cartesia

    async def transcribe_deepgram(self, pcm: np.ndarray, sample_rate: int) -> Transcript:
        from deepgram import PrerecordedOptions

        wav = _pcm_to_wav(pcm, sample_rate)
        with span("stt.deepgram", bytes=len(wav), model=self._settings.deepgram_model) as _sid:
            t0 = time.perf_counter()
            client = self._deepgram_client()
            opts = PrerecordedOptions(
                model=self._settings.deepgram_model,
                smart_format=True,
                punctuate=True,
                language="en-US",
            )

            def _call() -> dict:
                # SDK is sync; push to thread so we don't block the loop.
                return client.listen.rest.v("1").transcribe_file({"buffer": wav}, opts)

            resp = await asyncio.to_thread(_call)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            try:
                alt = resp.results.channels[0].alternatives[0]
                text = (alt.transcript or "").strip()
                conf = float(getattr(alt, "confidence", 0.0) or 0.0)
            except Exception as exc:
                log_event("stt.deepgram.parse_error", error=repr(exc))
                text = ""
                conf = 0.0
            return Transcript(
                text=text, provider="deepgram", latency_ms=latency_ms, confidence=conf
            )

    async def transcribe_cartesia(self, pcm: np.ndarray, sample_rate: int) -> Transcript:
        wav = _pcm_to_wav(pcm, sample_rate)
        with span("stt.cartesia", bytes=len(wav), model=self._settings.cartesia_stt_model):
            t0 = time.perf_counter()
            client = self._cartesia_client()

            def _call() -> str:
                result = client.stt.transcribe(
                    file=("audio.wav", wav, "audio/wav"),
                    model=self._settings.cartesia_stt_model,
                    language="en",
                )
                # SDK returns an object with `.text`; some versions return a dict.
                if isinstance(result, dict):
                    return str(result.get("text", "")).strip()
                return str(getattr(result, "text", "") or "").strip()

            try:
                text = await asyncio.to_thread(_call)
            except Exception as exc:
                log_event("stt.cartesia.error", error=repr(exc))
                text = ""
            latency_ms = int((time.perf_counter() - t0) * 1000)
            return Transcript(text=text, provider="cartesia", latency_ms=latency_ms)

    async def transcribe(self, pcm: np.ndarray, sample_rate: int) -> Transcript:
        """Unified entry point. Tries Deepgram first, falls back to Cartesia
        only on hard failure — not on low confidence, since Deepgram's
        confidence score is already well-calibrated for our use case."""
        if self._settings.deepgram_api_key:
            try:
                return await self.transcribe_deepgram(pcm, sample_rate)
            except Exception as exc:
                log_event("stt.deepgram.failed", error=repr(exc))
        return await self.transcribe_cartesia(pcm, sample_rate)


_stt: STT | None = None


def stt() -> STT:
    global _stt
    if _stt is None:
        _stt = STT()
    return _stt
