from __future__ import annotations

import numpy as np

from mira.obs.logging import log_event

_SAMPLE_RATE = 24000


def _tone(freq_hz: float, duration_ms: int, amplitude: float = 0.18) -> np.ndarray:
    n = int(_SAMPLE_RATE * duration_ms / 1000)
    t = np.arange(n) / _SAMPLE_RATE
    wave = amplitude * np.sin(2 * np.pi * freq_hz * t)
    # Short fade-in/out so it never clicks.
    fade = min(480, n // 8)
    if fade > 0:
        ramp = np.linspace(0.0, 1.0, fade)
        wave[:fade] *= ramp
        wave[-fade:] *= ramp[::-1]
    return wave.astype(np.float32)


def _play(wave: np.ndarray) -> None:
    try:
        import sounddevice as sd

        sd.play(wave, samplerate=_SAMPLE_RATE, blocking=True)
    except Exception as exc:
        log_event("chime.error", error=repr(exc))


def play_start_chime() -> None:
    """Short rising two-tone ping played right after wake-word trigger."""
    a = _tone(660, 70)
    b = _tone(990, 90)
    _play(np.concatenate([a, b]))


def play_end_chime() -> None:
    """Short falling two-tone ping played after capture stops."""
    a = _tone(880, 60)
    b = _tone(560, 80)
    _play(np.concatenate([a, b]))
