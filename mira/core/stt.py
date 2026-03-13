"""
stt.py
Batch-style STT for Bujji/Mira using Cartesia Ink-Whisper over WebSockets.
Falls back to OpenAI Whisper if the transcript is too short/incomplete.
"""

import numpy as np
import time
import io
import soundfile as sf
from scipy.signal import resample_poly
from cartesia import Cartesia
from mira.core.config import cfg
from mira.core import recorder
import openai

# ---- Init Clients -----------------------------------------------------------
client = Cartesia(api_key=cfg.CARTESIA_API_KEY)
openai.api_key = cfg.OPENAI_API_KEY

# ---- Constants --------------------------------------------------------------
MODEL_RATE = 16000   # ✅ Cartesia/OpenAI Whisper expect 16k


# ---- Utility: resample safely ----------------------------------------------
def ensure_16k(audio_np: np.ndarray, orig_sr: int) -> np.ndarray:
    """
    Resample input audio to 16 kHz mono float32.
    Handles both int16 PCM (from recorder) and float32 inputs.
    """
    if audio_np is None or audio_np.size == 0:
        return np.array([], dtype=np.float32)

    if audio_np.dtype == np.int16:
        audio_np = audio_np.astype(np.float32) / 32768.0

    if orig_sr == MODEL_RATE:
        return audio_np.astype(np.float32)

    # polyphase resample: up=MODEL_RATE, down=orig_sr
    return resample_poly(audio_np.astype(np.float32), MODEL_RATE, orig_sr)

# ---- Primary: Cartesia Ink-Whisper -----------------------------------------
def transcribe_ink(audio_np: np.ndarray, orig_sr: int) -> str:
    """Stream audio to Cartesia Ink-Whisper and return transcript."""
    if audio_np is None or audio_np.size == 0:
        return ""

    try:
        audio_16k = ensure_16k(audio_np, orig_sr=orig_sr)
        audio_int16 = (audio_16k * 32767).astype(np.int16).tobytes()

        chunk_size = int(MODEL_RATE * 0.02 * 2)  # 20ms @16k = 640 bytes
        chunks = [audio_int16[i:i + chunk_size] for i in range(0, len(audio_int16), chunk_size)]

        ws = client.stt.websocket(
            model="ink-whisper",
            language="en",
            encoding="pcm_s16le",
            sample_rate=MODEL_RATE,
            min_volume=0.08,                # 🔺 stricter silence threshold
            max_silence_duration_secs=1.2,  # 🔺 cut off quicker
        )

        final_text = ""
        for chunk in chunks:
            ws.send(chunk)
            time.sleep(0.02)
        ws.send("done")

        for result in ws.receive():
            if result["type"] == "transcript":
                candidate = (result.get("text") or "").strip()
                conf = result.get("confidence", 1.0)

                # 🔒 stricter filter: >0.75 confidence, >=2 words
                if candidate and conf > 0.75 and len(candidate.split()) >= 2:
                    final_text = candidate
            elif result["type"] == "done":
                break
        ws.close()

        return final_text.strip()
    except Exception as e:
        print(f"⚠️ Cartesia transcription failed: {e}")
        return ""

# ---- Fallback: OpenAI Whisper ----------------------------------------------
def transcribe_openai(audio_np: np.ndarray, orig_sr: int) -> str:
    """Send audio to OpenAI Whisper API as a fallback."""
    if audio_np is None or audio_np.size == 0:
        return ""

    try:
        audio_16k = ensure_16k(audio_np, orig_sr=orig_sr)
        buf = io.BytesIO()
        buf.name = "audio.wav"
        sf.write(buf, audio_16k, MODEL_RATE, format="WAV", subtype="PCM_16")
        buf.seek(0)

        resp = openai.audio.transcriptions.create(
            model="gpt-4o-transcribe",  # or "whisper-1"
            file=buf
        )
        return (resp.text or "").strip()
    except Exception as e:
        print(f"⚠️ OpenAI Whisper transcription failed: {e}")
        return ""


# ---- High-level Listener ---------------------------------------------------
def listen_once_ink(timeout_s: int = 10) -> str:
    """
    Record until silence (or timeout), then run hybrid transcription.
    """
    audio = recorder.record_until_silence(max_duration_sec=timeout_s)
    if audio is None or audio.size == 0:
        return ""

    sr = getattr(cfg, "RATE", 16000)

    transcript = transcribe_ink(audio, orig_sr=sr)

    # 🔒 junk filter: fallback to Whisper if empty or too short
    if not transcript or len(transcript.split()) < 2:
        transcript = transcribe_openai(audio, orig_sr=sr)

        # 🔒 apply same guard again after Whisper
        if not transcript or len(transcript.split()) < 2:
            return ""

    return transcript.strip()


# ---- Example Usage ---------------------------------------------------------
if __name__ == "__main__":
    print("🎤 Speak now (Ink-Whisper hybrid)…")
    text = listen_once_ink(timeout_s=6)
    if text:
        print(f"✅ Transcript: {text}")
    else:
        print("⚠️ No speech detected.")
