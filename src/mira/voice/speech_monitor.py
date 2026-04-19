from __future__ import annotations

import queue
import threading
import time
from typing import Callable

import numpy as np

from mira.config.settings import get_settings
from mira.obs.logging import log_event
from mira.runtime.bus import bus


class SpeechMonitor:
    """Fire-once voice-activity detector that runs during TTS playback.

    Purpose: enable barge-in. The wake-word listener is paused while MIRA is
    speaking (speaker audio would otherwise ring the keyword), so we need a
    separate ear to notice if the user starts talking over us. First real
    speech → publishes `speech.started` on the bus; TTS already subscribes
    to that and cancels playback.

    No AEC in this batch. Mitigations that keep this useful anyway:

      * High VAD aggressiveness (3) to reject background noise.
      * Energy-gate above an empirical RMS floor — drops low-level speaker
        echo that VAD alone sometimes accepts.
      * `grace_ms` start delay — the first tenths of a second of TTS often
        include leading-edge artifacts that look like voiced frames.
      * `min_voiced_ms` of ~300 so a single chirp of echo doesn't trigger.

    If these mitigations prove too aggressive or too permissive, the
    thresholds are the only knobs — the rest of the pipeline is unchanged.
    """

    _ENERGY_FLOOR = 800.0       # int16 RMS; tune with field data
    _GRACE_MS = 250
    _MIN_VOICED_MS = 300

    def __init__(
        self,
        *,
        on_trigger: Callable[[], None] | None = None,
    ) -> None:
        self._settings = get_settings()
        self._on_trigger = on_trigger
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._fired = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._fired.clear()
        self._thread = threading.Thread(
            target=self._run, name="speech-monitor", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._thread = None

    def fired(self) -> bool:
        return self._fired.is_set()

    def _run(self) -> None:
        import sounddevice as sd
        import webrtcvad

        sr = self._settings.sample_rate
        frame_ms = 20
        frame_len = int(sr * frame_ms / 1000)
        vad = webrtcvad.Vad(3)  # max aggressiveness; echo is our noise

        min_voiced_frames = max(1, self._MIN_VOICED_MS // frame_ms)
        grace_frames = max(0, self._GRACE_MS // frame_ms)

        q: queue.Queue[np.ndarray] = queue.Queue()

        def _cb(indata, frames, time_info, status) -> None:  # noqa: ANN001
            if status:
                log_event("speech_monitor.status", status=str(status))
            q.put(indata.copy().reshape(-1).astype(np.int16))

        device = self._settings.audio_input_device
        voiced_streak = 0
        seen_frames = 0
        t0 = time.perf_counter()

        try:
            stream = sd.InputStream(
                samplerate=sr,
                channels=1,
                dtype="int16",
                blocksize=frame_len,
                callback=_cb,
                device=device if device not in (None, "") else None,
            )
        except Exception as exc:
            # Audio device may be busy (TTS sometimes holds it on shared cards).
            # Don't crash — just skip barge-in for this turn.
            log_event("speech_monitor.open_error", error=repr(exc))
            return

        with stream:
            while not self._stop.is_set():
                try:
                    frame = q.get(timeout=0.2)
                except queue.Empty:
                    continue
                seen_frames += 1
                if seen_frames <= grace_frames:
                    continue

                if len(frame) < frame_len:
                    pad = np.zeros(frame_len - len(frame), dtype=np.int16)
                    frame = np.concatenate([frame, pad])
                elif len(frame) > frame_len:
                    frame = frame[:frame_len]

                rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
                is_voiced = rms >= self._ENERGY_FLOOR and vad.is_speech(
                    frame.tobytes(), sr
                )

                if is_voiced:
                    voiced_streak += 1
                    if voiced_streak >= min_voiced_frames:
                        self._fired.set()
                        elapsed_ms = int((time.perf_counter() - t0) * 1000)
                        log_event(
                            "speech_monitor.triggered",
                            elapsed_ms=elapsed_ms,
                            rms=rms,
                        )
                        bus().publish_nowait("speech.started")
                        if self._on_trigger is not None:
                            try:
                                self._on_trigger()
                            except Exception as exc:
                                log_event(
                                    "speech_monitor.on_trigger_error",
                                    error=repr(exc),
                                )
                        return
                else:
                    voiced_streak = 0
