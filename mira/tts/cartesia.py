"""
tts.py
Real-time TTS for Bujji/Mira using Cartesia WebSockets (safe, stoppable).
"""

import sounddevice as sd
import json
import uuid
import base64
import numpy as np
from websocket import create_connection, WebSocketTimeoutException
from mira.core.config import cfg
import threading

# 🔹 global flag for barge-in
_is_speaking = threading.Event()

# ---- Constants --------------------------------------------------------------
MODEL_RATE = 16000    # ✅ Cartesia requires 16k


def stop():
    """Externally callable stop — cuts off TTS mid-stream."""
    if _is_speaking.is_set():
        print("🛑 stop() called — clearing speaking flag.")
    _is_speaking.clear()


def speak_streaming(
    text: str,
    voice: str | None = None,
    model: str = "sonic-2",
    sample_rate: int = MODEL_RATE,  # Cartesia output
    language: str = "en",
):
    if not text.strip():
        return b""

    # Pick voice (id or name)
    voice = voice or getattr(cfg, "CARTESIA_VOICE", None) or "verse"
    voice_obj = {"mode": "id", "id": voice} if "-" in voice else {"mode": "name", "name": voice}

    # Unique context per generation (conversation turn)
    context_id = str(uuid.uuid4())

    # Connect to Cartesia TTS WebSocket
    url = (
        f"wss://api.cartesia.ai/tts/websocket"
        f"?api_key={cfg.CARTESIA_API_KEY}&cartesia_version=2025-04-16"
    )
    ws = create_connection(url, timeout=10)

    # Request config
    request = {
        "model_id": model,
        "transcript": text,
        "voice": voice_obj,
        "language": language,
        "context_id": context_id,
        "output_format": {
            "container": "raw",
            "encoding": "pcm_s16le",
            "sample_rate": sample_rate,  # ✅ 16k for Cartesia
        },
        "add_timestamps": False,
        "continue": False,
    }
    ws.send(json.dumps(request))

    audio_bytes = bytearray()
    _is_speaking.set()

    try:
        # 🎧 Play directly at 16k (if your output device supports it)
        with sd.OutputStream(
            samplerate=MODEL_RATE,
            channels=2,          # ✅ duplicate mono → stereo
            dtype="int16",
            blocksize=1024,
            latency="low",
        ) as stream:
            print(f"🗣️ Speaking with {model}, voice={voice}")

            while _is_speaking.is_set():
                try:
                    msg = ws.recv()
                except WebSocketTimeoutException:
                    print("⚠️ TTS WebSocket timeout.")
                    break
                if not msg:
                    break

                data = json.loads(msg)

                if data.get("type") == "chunk":
                    # 🔹 Raw Cartesia audio (16k mono PCM16)
                    pcm_16k = np.frombuffer(base64.b64decode(data["data"]), dtype=np.int16)
                    audio_bytes.extend(pcm_16k.tobytes())

                    # 🔹 Convert mono → stereo
                    pcm_stereo = np.repeat(pcm_16k[:, None], 2, axis=1)

                    # 🎵 Play
                    stream.write(pcm_stereo)

                elif data.get("type") == "done":
                    print("✅ TTS complete")
                    break
                elif data.get("type") == "error":
                    print(f"❌ TTS Error: {data}")
                    break

            else:
                # speaking flag was cleared
                print("🛑 Barge-in stop triggered, cutting TTS.")

    finally:
        try:
            ws.close()
        except Exception:
            pass
        _is_speaking.clear()

    return bytes(audio_bytes)


# ---- Wrapper (for brain.py) -------------------------------------------------
def speak(text: str, **kwargs):
    """Public wrapper — use this instead of calling speak_streaming directly."""
    return speak_streaming(text, **kwargs)


# ---- Example Usage ----------------------------------------------------------
if __name__ == "__main__":
    text = "Hello, Mira! Mira voice assistant is now speaking with Cartesia Sonic-2 in real time."
    audio = speak(text, model="sonic-2")
    print(f"✅ Spoke {len(audio)} bytes of audio")
