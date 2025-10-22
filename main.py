import os
import sys
import threading
import signal

# Redirect browser_use temp/downloads away from /tmp
os.environ["BROWSER_USE_DOWNLOADS_PATH"] = "/Volumes/HDD-1/LLM_projects/MiraVoiceAssistant/data/browser_downloads"

from mira.core.config import cfg
from mira.utils import logger, audio_utils
from mira.core import stt, brain
from mira.tts import cartesia
from mira.core.brain import _music_auto   # for CTRL+C stop

# -------------------------------------------------------------------
# State flags
# -------------------------------------------------------------------
is_processing = threading.Event()
interaction_count = 0

# -------------------------------------------------------------------
# Config checks
# -------------------------------------------------------------------
def _check_keys():
    missing = []
    if not getattr(cfg, "CARTESIA_API_KEY", None):
        missing.append("CARTESIA_API_KEY")
    if not getattr(cfg, "CARTESIA_VOICE", None):
        missing.append("CARTESIA_VOICE")
    if missing:
        msg = f"Missing config keys: {', '.join(missing)}"
        logger.log_event("ConfigWarning", msg)
        print("⚠️", msg)
        raise RuntimeError(msg)

# -------------------------------------------------------------------
# Multi-turn pipeline
# -------------------------------------------------------------------
def run_once(greet: bool = False):
    global interaction_count

    if greet:
        print("[DEBUG] Greeting now…")
        cartesia.speak(f"Hi {cfg.USER_NAME}")

    audio_utils.play_start_chime(rate=cfg.RATE)
    max_sec = getattr(cfg, "MAX_RECORD_SEC", 15)
    text = stt.listen_once_ink(timeout_s=max_sec)
    audio_utils.play_end_chime(rate=cfg.RATE)

    clean = (text or "").strip()
    if not clean:
        logger.log_event("Transcription", "[no speech]")
        return

    logger.log_event("Transcription", clean)
    brain.run_interaction(clean, max_turns=3)

    # prune memory every 50 turns
    interaction_count += 1
    if interaction_count % 50 == 0:
        if hasattr(brain, "memory") and hasattr(brain.memory, "prune_chats"):
            brain.memory.prune_chats(max_age_days=30, keep_last=1000)

# -------------------------------------------------------------------
# Guarded runner
# -------------------------------------------------------------------
def run_once_guarded(greet: bool = False):
    if is_processing.is_set():
        print("⚠️ Ignored wake word — already handling a query.")
        return
    is_processing.set()
    try:
        run_once(greet=greet)
    finally:
        is_processing.clear()

#
# -------------------------------------------------------------------
# Always-on wakeword listener (ONNX)
# -------------------------------------------------------------------
def run_always_on():
    from mira.core import wakeword  # 👈 Porcupine wrapper (hey-amma.ppn)

    def on_wake():
        logger.log_event("Wakeword", "M.I.R.A triggered")
        run_once_guarded(greet=True)

    print("🟢 Mira is running (Porcupine). Say 'Hey Mira' to start.")

    try:
        wakeword.listen_forever(on_wake)
    except Exception as e:
        logger.log_error(e, context="wakeword.listen_forever")
        print("⚠️ Wakeword listener crashed.")

# Entrypoint
# -------------------------------------------------------------------
def main():
    _check_keys()

    if hasattr(brain, "memory") and hasattr(brain.memory, "prune_chats"):
        brain.memory.prune_chats(max_age_days=30, keep_last=1000)

    run_always_on()

# -------------------------------------------------------------------
# Custom SIGINT handler (CTRL+C)
# -------------------------------------------------------------------
def handle_sigint(signum, frame):
    print("\n🛑 CTRL+C → stopping music only, MIRA still alive.")

    try:
        if _music_auto and _music_auto.available():
            _music_auto.stop()
            print("⏹️ Music stopped.")
    except Exception as e:
        print(f"⚠️ Music stop failed: {e}")

    try:
        cartesia.stop()
    except Exception as e:
        print(f"⚠️ TTS stop failed: {e}")

    # ✅ no sys.exit here — process keeps running


signal.signal(signal.SIGINT, handle_sigint)

# -------------------------------------------------------------------
# Main entrypoint
# -------------------------------------------------------------------
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.log_error(e, context="amma.main")
        raise
