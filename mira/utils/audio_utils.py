import numpy as np
import sounddevice as sd
import soundfile as sf
import os

# 🔊 Play a short beep (start listening)
def play_start_chime(freq=880, duration=0.2, rate=16000):
    t = np.linspace(0, duration, int(rate * duration), False)
    tone = 0.2 * np.sin(freq * t * 2 * np.pi)
    sd.play(tone, rate)
    sd.wait()

# 🔊 Play a short beep (stop listening)
def play_end_chime(freq=440, duration=0.3, rate=16000):
    t = np.linspace(0, duration, int(rate * duration), False)
    tone = 0.2 * np.sin(freq * t * 2 * np.pi)
    sd.play(tone, rate)
    sd.wait()

# 🎚️ Normalize audio to -1.0 .. 1.0 float32
def normalize_audio(pcm16: np.ndarray) -> np.ndarray:
    if pcm16.dtype != np.float32:
        pcm16 = pcm16.astype(np.float32) / 32768.0
    return np.clip(pcm16, -1.0, 1.0)

# 💾 Save numpy audio to WAV
def save_wav(filename: str, audio: np.ndarray, rate: int = 16000):
    sf.write(filename, audio, rate)

# 📂 Ensure data directory exists
def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)
