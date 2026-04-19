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
from mira.ui.avatar import MiraAvatar

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
def run_once(avatar=None, greet: bool = False):
    global interaction_count

    if greet:
        print("[DEBUG] Greeting now…")
        if avatar:
            avatar.set_mode("speaking")
        cartesia.speak(f"Hi {cfg.USER_NAME}")

    if avatar:
        avatar.set_mode("listening")
    audio_utils.play_start_chime(rate=cfg.RATE)

    max_sec = getattr(cfg, "MAX_RECORD_SEC", 15)
    text = stt.listen_once_ink(timeout_s=max_sec)

    audio_utils.play_end_chime(rate=cfg.RATE)
    if avatar:
        avatar.set_mode("thinking")

    clean = (text or "").strip()
    if not clean:
        logger.log_event("Transcription", "[no speech]")
        if avatar:
            avatar.set_mode("idle")
        return

    logger.log_event("Transcription", clean)
    brain.run_interaction(clean, max_turns=3)

    if avatar:
        avatar.set_mode("speaking")

    # prune memory every 50 turns
    interaction_count += 1
    if interaction_count % 50 == 0:
        if hasattr(brain, "memory") and hasattr(brain.memory, "prune_chats"):
            brain.memory.prune_chats(max_age_days=30, keep_last=1000)

    if avatar:
        avatar.set_mode("idle")

# -------------------------------------------------------------------
# Guarded runner
# -------------------------------------------------------------------
def run_once_guarded(avatar=None, greet: bool = False):
    if is_processing.is_set():
        print("⚠️ Ignored wake word — already handling a query.")
        return
    is_processing.set()
    try:
        run_once(avatar=avatar, greet=greet)
    finally:
        is_processing.clear()

# -------------------------------------------------------------------
# Always-on wakeword listener (ONNX)
# -------------------------------------------------------------------
def run_always_on(avatar=None):
    from mira.core import wakeword  # 👈 Porcupine wrapper (hey-amma.ppn)

    def on_wake():
        logger.log_event("Wakeword", "M.I.R.A triggered")
        run_once_guarded(avatar=avatar, greet=True)

    print("🟢 MIRA is running (Porcupine). Say 'Hey Mira' to start.")

    try:
        if avatar:
            avatar.set_mode("idle")
        wakeword.listen_forever(on_wake)
    except Exception as e:
        logger.log_error(e, context="wakeword.listen_forever")
        print("⚠️ Wakeword listener crashed.")

# -------------------------------------------------------------------
# Entrypoint
# -------------------------------------------------------------------
def main():
    _check_keys()
    if hasattr(brain, "memory") and hasattr(brain.memory, "prune_chats"):
        brain.memory.prune_chats(max_age_days=30, keep_last=1000)

    avatar = MiraAvatar()
    brain.avatar = avatar
    avatar.active = True

    # run wake-word in background
    threading.Thread(target=lambda: run_always_on(avatar), daemon=True).start()

    # keep avatar window alive in foreground
    avatar._loop()


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

signal.signal(signal.SIGINT, handle_sigint)

# -------------------------------------------------------------------
# Main entrypoint
# -------------------------------------------------------------------
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.log_error(e, context="mira.main")
        raise
