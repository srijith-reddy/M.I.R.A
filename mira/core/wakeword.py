import os
import struct
import time   # ✅ FIX: import time for sounddevice callback
import sounddevice as sd
from pvporcupine import create
from mira.core.config import cfg


def listen_for_wakeword(callback):
    """Listen for custom Porcupine wakeword and trigger callback when detected."""

    porcupine = create(
        access_key=cfg.PICOVOICE_ACCESS_KEY,
        keyword_paths=[cfg.WAKEWORD_MODEL],
        sensitivities=[cfg.WAKEWORD_SENSITIVITY],
    )

    detected = False

    def audio_callback(indata, frames, time_info, status):
        nonlocal detected
        if status:
            print("⚠️ Audio status:", status)

        # convert raw bytes -> 16-bit PCM
        pcm = struct.unpack_from("h" * (len(indata) // 2), indata)

        # Porcupine only works if frame length matches exactly
        if len(pcm) == porcupine.frame_length:
            result = porcupine.process(pcm)
            if result >= 0 and not detected:
                print("👂 Wake word detected!")
                detected = True
                raise sd.CallbackStop  # stop input stream

    with sd.RawInputStream(
        device=cfg.DEVICE or None,
        samplerate=porcupine.sample_rate,
        blocksize=porcupine.frame_length,
        dtype="int16",
        channels=1,
        callback=audio_callback,
    ):
        print("🟢 Listening for wake word (Porcupine)...")
        try:
            while not detected:
                sd.sleep(100)  # keep polling
        except KeyboardInterrupt:
            print("\n🛑 Stopped listening.")
        finally:
            porcupine.delete()

    if detected:
        callback()


def listen_forever(callback):
    while True:
        listen_for_wakeword(callback)
