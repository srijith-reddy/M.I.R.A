from __future__ import annotations

import os
import queue
import threading
from typing import Any, Callable, Protocol

from mira.config.settings import get_settings
from mira.obs.logging import log_event
from mira.runtime.bus import bus
from mira.runtime.tracing import span


class WakeDetector(Protocol):
    """Shared interface so the VoiceLoop doesn't care which backend runs."""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...


class _PauseCounter:
    """Refcounted pause flag. A plain threading.Event broke under nested
    pause/resume pairs: the inner `finally: resume()` clears the flag while
    the outer context still expects to be paused, so wake detection turned
    back on mid-TTS and the speaker audio self-triggered the wake word.

    Counter semantics: `is_paused()` stays true as long as any pauser
    hasn't resumed. Resume is clamped at zero so a stray resume() can't
    drive the count negative and mask a later pause."""

    def __init__(self) -> None:
        self._depth = 0
        self._lock = threading.Lock()

    def increment(self) -> None:
        with self._lock:
            self._depth += 1

    def decrement(self) -> None:
        with self._lock:
            if self._depth > 0:
                self._depth -= 1

    def is_paused(self) -> bool:
        # Reads of a single int are atomic in CPython; skipping the lock on
        # the detector hot path keeps per-frame overhead at zero.
        return self._depth > 0


class PorcupineWakeWord:
    """Always-on wake-word listener using Picovoice Porcupine.

    Design notes:
      * Runs in a daemon thread — the main asyncio loop stays free.
      * Releases the input audio stream before invoking `on_wake`, so the
        recorder can take over the mic without contention, then reopens once
        `on_wake` returns. This is the exact contention bug that bit v1.
      * Publishes `wake.triggered` on the event bus so any interested
        subsystem (UI glow, chime, metrics) can react.
    """

    def __init__(
        self,
        on_wake: Callable[[], None],
        keyword: str = "jarvis",
    ) -> None:
        self._on_wake = on_wake
        self._keyword = keyword
        self._settings = get_settings()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._paused = _PauseCounter()
        self._porcupine = None
        self._recorder = None

    def pause(self) -> None:
        """Keep the audio stream open but stop running wake-word detection.
        Used during TTS playback so speaker audio can't self-trigger the wake
        word. Cheaper than stop/start which recreates the Porcupine handle.
        Refcounted: nested pause/resume pairs stack correctly."""
        self._paused.increment()

    def resume(self) -> None:
        self._paused.decrement()

    def _open(self) -> None:
        import pvporcupine
        from pvrecorder import PvRecorder

        if not self._settings.picovoice_access_key:
            raise RuntimeError(
                "PICOVOICE_ACCESS_KEY is not set; wake-word listener cannot start."
            )
        self._porcupine = pvporcupine.create(
            access_key=self._settings.picovoice_access_key,
            keywords=[self._keyword],
            sensitivities=[self._settings.wakeword_sensitivity],
        )
        self._recorder = PvRecorder(
            frame_length=self._porcupine.frame_length,
            device_index=-1,
        )
        self._recorder.start()

    def _close(self) -> None:
        if self._recorder is not None:
            try:
                self._recorder.stop()
                self._recorder.delete()
            except Exception:
                pass
            self._recorder = None
        if self._porcupine is not None:
            try:
                self._porcupine.delete()
            except Exception:
                pass
            self._porcupine = None

    def _loop(self) -> None:
        # Outer supervisor: a PortAudio hiccup (AirPods connect, another app
        # grabs the mic, coreaudiod wake-from-sleep) used to kill this thread
        # and leave the daemon silently deaf. Retry with exponential backoff
        # so the detector self-heals.
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._open()
                log_event("wakeword.ready", keyword=self._keyword)
                backoff = 1.0
                while not self._stop.is_set():
                    assert self._recorder is not None and self._porcupine is not None
                    pcm = self._recorder.read()
                    if self._paused.is_paused():
                        # Drain the buffer but skip detection so TTS audio can't
                        # ring the wake word while we're talking to the user.
                        continue
                    idx = self._porcupine.process(pcm)
                    if idx >= 0:
                        with span("wakeword.trigger", keyword=self._keyword):
                            bus().publish_nowait("wake.triggered", keyword=self._keyword)
                            # Release the mic before handing control off.
                            self._recorder.stop()
                            try:
                                self._on_wake()
                            except Exception as exc:
                                log_event("wakeword.on_wake_error", error=repr(exc))
                            # Reopen for the next trigger.
                            if not self._stop.is_set():
                                self._recorder.start()
            except Exception as exc:
                log_event("wakeword.fatal", error=repr(exc), retry_in_s=round(backoff, 1))
            finally:
                self._close()
            if self._stop.is_set() or self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, 30.0)
            log_event("wakeword.restart", keyword=self._keyword)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="wakeword", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None


class OpenWakeWordDetector:
    """Free, local wake-word backend (openwakeword). Same surface as
    PorcupineWakeWord so VoiceLoop can swap blindly.

    Runs sounddevice in callback mode on a dedicated daemon thread:
      * 16kHz mono int16, 1280-sample frames (80ms) — openwakeword's
        native input size. Feeding different frame sizes forces its
        internal melspectrogram buffer to re-align and tanks accuracy.
      * Model ref is either a stock name ("hey_jarvis", "alexa",
        "hey_mycroft", "computer") or an absolute / ./-prefixed path
        to a custom `.onnx` / `.tflite` file. The former downloads on
        first use into ~/.cache/openwakeword.
      * Threshold default 0.5 matches the library's own docs; tune via
        `wakeword_sensitivity` — we reuse that setting so the user only
        has one knob regardless of backend.
    """

    _FRAME = 1280  # 80ms @ 16kHz — openwakeword's required chunk size.
    _SR = 16000

    def __init__(
        self,
        on_wake: Callable[[], None],
        model_ref: str = "hey_jarvis",
    ) -> None:
        self._on_wake = on_wake
        self._model_ref = model_ref
        self._settings = get_settings()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._paused = _PauseCounter()
        self._model: Any | None = None
        self._stream: Any | None = None
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=32)

    def pause(self) -> None:
        self._paused.increment()

    def resume(self) -> None:
        self._paused.decrement()

    def _load_model(self) -> Any:
        from openwakeword.model import Model

        ref = self._model_ref.strip()
        if ref.endswith(".onnx") or ref.endswith(".tflite") or os.path.sep in ref:
            # Absolute / relative path → pass through as custom model.
            return Model(wakeword_models=[ref])
        return Model(wakeword_models=[ref])

    def _audio_cb(self, indata: Any, _frames: int, _time: Any, _status: Any) -> None:
        try:
            self._queue.put_nowait(bytes(indata))
        except queue.Full:
            # Dropping a frame is better than stalling the PortAudio callback —
            # backlog means detection is already behind the live mic anyway.
            pass

    def _loop(self) -> None:
        import numpy as np
        import sounddevice as sd

        # See PorcupineWakeWord._loop — same self-healing rationale.
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._model = self._load_model()
                self._stream = sd.RawInputStream(
                    samplerate=self._SR,
                    blocksize=self._FRAME,
                    dtype="int16",
                    channels=1,
                    callback=self._audio_cb,
                )
                self._stream.start()
                threshold = float(self._settings.wakeword_sensitivity)
                log_event(
                    "wakeword.ready",
                    backend="openwakeword",
                    model=self._model_ref,
                    threshold=threshold,
                )
                backoff = 1.0
                while not self._stop.is_set():
                    try:
                        buf = self._queue.get(timeout=0.25)
                    except queue.Empty:
                        continue
                    if self._paused.is_paused():
                        continue
                    arr = np.frombuffer(buf, dtype=np.int16)
                    scores = self._model.predict(arr)
                    # predict returns {model_key: score}; take the max so custom
                    # and stock model keys both work without branching here.
                    best = max(scores.values()) if scores else 0.0
                    self._debug_n = getattr(self, "_debug_n", 0) + 1
                    if self._debug_n % 50 == 0:
                        import numpy as _np
                        rms = float(_np.sqrt(_np.mean(arr.astype(_np.float32) ** 2))) / 32768.0
                        log_event("wakeword.tick", rms=round(rms, 4), score=round(best, 3))
                    if best >= threshold:
                        with span("wakeword.trigger", backend="openwakeword"):
                            bus().publish_nowait(
                                "wake.triggered", backend="openwakeword", score=best
                            )
                            # Release the stream so the recorder can grab the mic.
                            self._stream.stop()
                            try:
                                self._on_wake()
                            except Exception as exc:
                                log_event("wakeword.on_wake_error", error=repr(exc))
                            if not self._stop.is_set():
                                # Drain stale frames captured during the turn —
                                # otherwise the next loop iteration feeds
                                # minute-old audio into the detector.
                                while not self._queue.empty():
                                    try:
                                        self._queue.get_nowait()
                                    except queue.Empty:
                                        break
                                self._stream.start()
            except Exception as exc:
                log_event(
                    "wakeword.fatal",
                    backend="openwakeword",
                    error=repr(exc),
                    retry_in_s=round(backoff, 1),
                )
            finally:
                if self._stream is not None:
                    try:
                        self._stream.stop()
                        self._stream.close()
                    except Exception:
                        pass
                    self._stream = None
                self._model = None
                # Drop frames captured on the dead stream so a reopened
                # stream doesn't process pre-error audio.
                while not self._queue.empty():
                    try:
                        self._queue.get_nowait()
                    except queue.Empty:
                        break
            if self._stop.is_set() or self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, 30.0)
            log_event("wakeword.restart", backend="openwakeword")

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="wakeword-oww", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None


def make_wakeword(on_wake: Callable[[], None]) -> WakeDetector:
    """Pick a backend based on settings. `auto` prefers Porcupine when the
    Picovoice key is present (better accuracy, paid model) and falls back to
    openwakeword otherwise so the daemon still boots on a fresh install."""
    s = get_settings()
    backend = (s.wakeword_backend or "auto").lower()
    if backend == "auto":
        backend = "porcupine" if s.picovoice_access_key else "openwakeword"
    if backend == "porcupine":
        return PorcupineWakeWord(on_wake=on_wake, keyword=s.wakeword_model or "jarvis")
    if backend == "openwakeword":
        return OpenWakeWordDetector(
            on_wake=on_wake, model_ref=s.wakeword_model or "hey_jarvis"
        )
    raise RuntimeError(f"unknown wakeword backend: {backend}")
