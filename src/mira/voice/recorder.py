from __future__ import annotations

import queue
import time
from dataclasses import dataclass

import numpy as np

from mira.config.settings import get_settings
from mira.obs.logging import log_event
from mira.runtime.bus import bus
from mira.runtime.tracing import span


@dataclass(frozen=True)
class Capture:
    pcm: np.ndarray  # int16 mono
    sample_rate: int
    duration_ms: int
    aborted: bool


def _frame_bytes(frame: np.ndarray) -> bytes:
    return frame.astype(np.int16).tobytes()


def record_until_silence(
    *,
    max_seconds: float = 20.0,
    min_voiced_ms: int = 200,
) -> Capture:
    """Blocking VAD-gated capture.

    Starts recording immediately, detects the first voiced segment, and stops
    after `STOP_MS` of trailing silence. Publishes `speech.started` /
    `speech.ended` on the bus so the UI can show a live indicator and the
    Supervisor can kick off speculative work (e.g., warm the planner model
    or start loading browser context) before STT finishes.

    The default `STOP_MS=700` is deliberately aggressive — we'd rather
    clip the last 50ms of a trailing word than wait two seconds after the
    user stops speaking. Re-prompts are cheap; perceived latency isn't.
    """
    import sounddevice as sd
    import webrtcvad

    settings = get_settings()
    sr = settings.sample_rate
    vad = webrtcvad.Vad(settings.vad_aggressiveness)
    frame_ms = 20
    frame_len = int(sr * frame_ms / 1000)
    stop_frames_needed = max(1, settings.stop_ms // frame_ms)
    min_voiced_frames = max(1, min_voiced_ms // frame_ms)
    tail_frames = max(0, settings.tail_ms // frame_ms)

    q: queue.Queue[np.ndarray] = queue.Queue()

    def _cb(indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            log_event("recorder.status", status=str(status))
        q.put(indata.copy().reshape(-1).astype(np.int16))

    # Smoothed RMS for the HUD orb. We emit ~15Hz (every ~66ms) so the
    # orb pulse feels fluid without drowning the websocket in frames.
    # Exponential smoothing keeps the level from twitching on plosives.
    last_emit_t = 0.0
    smoothed_level = 0.0
    EMIT_INTERVAL_S = 1 / 15

    collected: list[np.ndarray] = []
    voiced_total = 0
    silent_tail = 0
    started_speech = False
    t0 = time.perf_counter()

    device = settings.audio_input_device

    with span("voice.record"):
        stream = sd.InputStream(
            samplerate=sr,
            channels=1,
            dtype="int16",
            blocksize=frame_len,
            callback=_cb,
            device=device if device not in (None, "") else None,
        )
        with stream:
            while True:
                if time.perf_counter() - t0 > max_seconds:
                    log_event("recorder.max_seconds", max_seconds=max_seconds)
                    break
                try:
                    frame = q.get(timeout=0.5)
                except queue.Empty:
                    continue

                if len(frame) < frame_len:
                    # Partial frame at end; pad so webrtcvad accepts it.
                    pad = np.zeros(frame_len - len(frame), dtype=np.int16)
                    frame = np.concatenate([frame, pad])
                elif len(frame) > frame_len:
                    frame = frame[:frame_len]

                is_voiced = vad.is_speech(_frame_bytes(frame), sr)
                collected.append(frame)

                # Level for the HUD orb. int16 RMS → 0..1, with a log
                # shape so quiet speech still moves the needle. We clamp
                # ceiling at ~0.3 RMS (around -10dBFS) because anything
                # louder already pegs the orb visually.
                now = time.perf_counter()
                if now - last_emit_t >= EMIT_INTERVAL_S:
                    rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2))) / 32768.0
                    # Log-scaled so the orb is lively in normal speech
                    # range rather than quietly idle until a shout.
                    scaled = min(1.0, rms / 0.08)
                    smoothed_level = 0.6 * smoothed_level + 0.4 * scaled
                    last_emit_t = now
                    log_event("voice.level", level=round(smoothed_level, 3))

                if is_voiced:
                    voiced_total += 1
                    silent_tail = 0
                    if not started_speech and voiced_total >= min_voiced_frames:
                        started_speech = True
                        bus().publish_nowait("speech.started")
                else:
                    if started_speech:
                        silent_tail += 1
                        if silent_tail >= stop_frames_needed:
                            break

        # Keep a short tail so we don't clip the final consonant.
        if tail_frames and len(collected) > tail_frames:
            # Nothing to do — we already captured through the silent tail.
            pass

    pcm = np.concatenate(collected) if collected else np.zeros(0, dtype=np.int16)
    duration_ms = int(len(pcm) / sr * 1000)
    aborted = not started_speech or voiced_total < min_voiced_frames

    if started_speech:
        bus().publish_nowait("speech.ended", duration_ms=duration_ms, aborted=aborted)
    log_event(
        "recorder.done",
        duration_ms=duration_ms,
        voiced_frames=voiced_total,
        aborted=aborted,
    )
    return Capture(pcm=pcm, sample_rate=sr, duration_ms=duration_ms, aborted=aborted)
