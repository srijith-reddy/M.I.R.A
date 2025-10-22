import math
import numpy as np
import sounddevice as sd
import queue
import webrtcvad
from mira.core.config import cfg

# --- Ensure valid params ---
assert cfg.RATE == 16000, "WebRTC VAD works best at 16kHz"
assert cfg.CHANNELS == 1, "Must use mono for VAD"

# valid block sizes for 16kHz: 160, 320, 480
if cfg.BLOCK_SIZE not in (160, 320, 480):
    raise ValueError(f"BLOCK_SIZE must be 160/320/480 for VAD, got {cfg.BLOCK_SIZE}")

BLOCK_MS = int((cfg.BLOCK_SIZE / cfg.RATE) * 1000)
STOP_BLOCKS = int(math.ceil(cfg.STOP_MS / BLOCK_MS))
TAIL_BLOCKS = int(math.ceil(getattr(cfg, "TAIL_MS", 0) / BLOCK_MS))

print(f"[INFO] RATE={cfg.RATE}, BLOCK={cfg.BLOCK_SIZE} ({BLOCK_MS}ms), stop={cfg.STOP_MS}ms, tail={getattr(cfg,'TAIL_MS',0)}ms")

q = queue.Queue()
vad = webrtcvad.Vad(cfg.VAD_AGGRESSIVENESS)


def audio_cb(indata, frames, time_info, status):
    """Callback from sounddevice, pushes frames into queue."""
    if status:
        print("⚠️ Audio status:", status)
    q.put(indata.copy())


def record_until_silence(max_duration_sec: float = None) -> np.ndarray:
    """
    Record audio until VAD detects silence for ~STOP_MS.
    Returns: mono float32 numpy array, normalized to [-1, 1].
    """
    speech_started = False
    speech_start_count = 0
    silence_count = 0
    frames_collected = 0
    captured = []

    max_frames = int((max_duration_sec * 1000.0) / BLOCK_MS) if max_duration_sec else None

    with sd.InputStream(
        samplerate=cfg.RATE,
        blocksize=cfg.BLOCK_SIZE,
        channels=1,
        dtype="int16",  # VAD expects PCM16
        callback=audio_cb,
    ):
        while True:
            try:
                block = q.get(timeout=1)
            except queue.Empty:
                print("❌ No audio received from device.")
                return np.array([], dtype=np.float32)

            frames_collected += 1
            if max_frames and frames_collected > max_frames:
                print("⏱️ Max duration hit, stopping.")
                break

            mono = block.reshape(-1)
            is_speech = vad.is_speech(mono.tobytes(), cfg.RATE)

            if not speech_started:
                if is_speech:
                    speech_start_count += 1
                    if speech_start_count >= 3:  # require ~3 voiced frames
                        print("🎙 Speech started")
                        speech_started = True
                        captured.append(mono)
                else:
                    speech_start_count = 0
            else:
                captured.append(mono)
                silence_count = silence_count + 1 if not is_speech else 0

                if silence_count >= STOP_BLOCKS:
                    print("🔇 Detected silence, stopping.")
                    # keep a small tail for natural cutoff
                    if TAIL_BLOCKS > 0 and len(captured) > TAIL_BLOCKS:
                        captured = captured[:-TAIL_BLOCKS]

                    # 🔴 stop TTS here
                    try:
                        import mira.tts as tts
                        tts.stop()
                    except Exception:
                        pass
                    break

    if not captured:
        print("⚠️ No speech detected.")
        return np.array([], dtype=np.float32)

    audio_int16 = np.concatenate(captured, axis=0)
    return audio_int16.astype(np.float32) / 32768.0



if __name__ == "__main__":
    print("🎤 Mic test starting...")
    audio = record_until_silence(max_duration_sec=5)
    if audio.size > 0:
        print(f"✅ Captured {len(audio)} samples (~{len(audio) / cfg.RATE:.2f} sec)")
    else:
        print("⚠️ No audio captured.")
