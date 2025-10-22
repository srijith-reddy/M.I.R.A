import os
from dotenv import load_dotenv

load_dotenv()

def _csv(key: str, default: str = ""):
    val = os.getenv(key, default)
    return [s.strip() for s in val.split(",") if s.strip()]

class Config:
    FOLLOWUP_WINDOW_S = 7        # how long to listen after speaking
    FOLLOWUP_WAKE_WORD_OK = True   # allow "Amma ..." to break out to wakeword loop
    FOLLOWUP_STOP_WORDS = {
    "no thanks",
    "stop",
    "cancel",
    "thanks",
    "thank you",
    "that's all",
    "thats all",   # STT may drop apostrophe
    "goodbye",
    "thanks mira",
    "im good",
    "i'm good"
}

    ASK_FOLLOWUP = True
    FOLLOWUP_LINES = [
        "Anything else you'd like me to do?",
        "Want me to keep going?",
        "Should I check anything else?",
        "Would you like more details?",
    ]
    # OpenAI
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-1")

    # Elevenlabs (TTS via Elevenlabs)
    CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY")
    CARTESIA_VOICE = os.getenv("CARTESIA_VOICE")

    # Wakeword (Porcupine .ppn)
    PICOVOICE_ACCESS_KEY = os.getenv("PICOVOICE_ACCESS_KEY", "").strip()
    WAKEWORD_MODEL = os.getenv("WAKEWORD_MODEL", "./assets/wakewords/Hey-Mira.ppn")
    WAKEWORD_SENSITIVITY = float(os.getenv("WAKEWORD_SENSITIVITY", "0.7"))
    
    # --- Audio ---
    RATE              = int(os.getenv("RATE", 16000))
    CHANNELS          = int(os.getenv("CHANNELS", 1))
    BLOCK_SIZE        = int(os.getenv("BLOCK_SIZE", 320))   # 20ms @ 16k
    VAD_AGGRESSIVENESS= int(os.getenv("VAD_AGGRESSIVENESS", 2))

    
    # --- Silence detection ---

    STOP_MS = int(os.getenv("STOP_MS", 2000))   # default 2000 if not set
    TAIL_MS = int(os.getenv("TAIL_MS", 100))      # default 100 if not set

    # User
    USER_NAME = os.getenv("USER_NAME", "Shrey")
    DEVICE = os.getenv("DEVICE", None)  # None = let sounddevice pick default

    # -----------------------------
    # Google (Gmail / Calendar)
    # -----------------------------
    GOOGLE_CLIENT_SECRET_FILE = os.getenv("GOOGLE_CLIENT_SECRET_FILE", "client_secret.json")
    GOOGLE_TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.json")  # cached user creds
    GOOGLE_APP_NAME = os.getenv("GOOGLE_APP_NAME", "MIRA")

    # Scopes (comma-separated in .env or defaults here)
    GOOGLE_SCOPES = _csv(
    "GOOGLE_SCOPES",
    "https://www.googleapis.com/auth/gmail.send,"
    "https://www.googleapis.com/auth/gmail.modify,"
    "https://www.googleapis.com/auth/contacts.readonly"
)


    # Service defaults
    GMAIL_USER = os.getenv("GMAIL_USER", "me")

    # OAuth behavior
    GOOGLE_OAUTH_PORT = int(os.getenv("GOOGLE_OAUTH_PORT", "0"))  # 0 = auto-pick free port

    PLAYWRIGHT_PROFILE = "/Volumes/HDD-1/mira-playwright"

cfg = Config()

print("DEBUG: Picovoice key loaded?", bool(cfg.PICOVOICE_ACCESS_KEY))
