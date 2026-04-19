from __future__ import annotations

from functools import lru_cache

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from mira.config.paths import paths


def _load_env_files() -> None:
    # Persistent config dir first (lowest precedence), then cwd (overrides).
    load_dotenv(paths.config_dir / ".env", override=False)
    load_dotenv(".env", override=True)


_load_env_files()


class Settings(BaseSettings):
    """
    Runtime configuration. Values come from (in order): process env,
    cwd .env, then <config_dir>/.env. All fields are optional so boot
    always succeeds; missing required keys are reported via
    `missing_required_keys()` and enforced by the layers that need them.
    """

    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
    )

    # Identity
    user_name: str = "friend"

    # OpenAI
    openai_api_key: str | None = None
    openai_planner_model: str = "gpt-4o"
    openai_classify_model: str = "gpt-4o-mini"
    whisper_model: str = "whisper-1"

    # Embedding provider for episodic memory. When true, use fastembed's
    # BGE-small-en-v1.5 locally (384-d, no API call, ~10ms/embed on CPU).
    # Falls back to OpenAI text-embedding-3-small when fastembed isn't
    # installed or the model fails to load. Existing OpenAI-embedded rows
    # coexist safely — recall() filters by the `embedding_model` column.
    local_embeddings: bool = True
    local_embedding_model: str = "BAAI/bge-small-en-v1.5"
    # Window used for transcript dedup. A new episode with the same
    # normalized transcript as any turn in this many hours is discarded.
    episode_dedup_hours: float = 6.0
    # Episodes older than this are pruned at startup. Profile rows never
    # expire — those are explicit long-lived facts. 0 disables pruning.
    episode_retention_days: int = 180

    # Anthropic — used as an alternate planner / supervisor model. Never on
    # the hot-latency streaming path since we don't stream Anthropic yet.
    anthropic_api_key: str | None = None
    anthropic_planner_model: str = "claude-sonnet-4-6"

    # Groq — cheap, fast OpenAI-compatible endpoint. Preferred for the
    # Tier-0 router when available (10x cheaper, ~2-3x lower latency).
    groq_api_key: str | None = None
    groq_router_model: str = "llama-3.1-8b-instant"

    # DeepSeek — OpenAI-compatible endpoint (api.deepseek.com). V3 is a
    # strong planner/supervisor at a fraction of gpt-4o pricing. To route
    # planner traffic here, set OPENAI_PLANNER_MODEL=deepseek-chat —
    # dispatch keys off the model name, not the field name.
    deepseek_api_key: str | None = None
    deepseek_planner_model: str = "deepseek-chat"

    # Deepgram (primary streaming STT)
    deepgram_api_key: str | None = None
    deepgram_model: str = "nova-3"

    # Cartesia
    cartesia_api_key: str | None = None
    cartesia_voice: str | None = None
    cartesia_tts_model: str = "sonic-2"
    cartesia_stt_model: str = "ink-whisper"

    # Wake word.
    #   * `wakeword_backend`: "auto" picks Porcupine when the Picovoice key
    #     is set, falls back to openwakeword otherwise. Override explicitly
    #     with "porcupine" or "openwakeword" to pin a backend.
    #   * `wakeword_model`: for Porcupine, a keyword name ("jarvis", "alexa",
    #     "computer", …); for openwakeword, a stock name ("hey_jarvis",
    #     "alexa", "hey_mycroft", "computer") or a path to a custom
    #     `.onnx`/`.tflite` file (e.g. `./models/hey_mira.onnx`).
    #   * `wakeword_sensitivity`: threshold in [0, 1]; 0.5 is a reasonable
    #     default for both backends.
    picovoice_access_key: str | None = None
    wakeword_backend: str = Field(default="auto", validation_alias="WAKEWORD_BACKEND")
    wakeword_model: str = Field(default="hey_jarvis", validation_alias="WAKEWORD_MODEL")
    wakeword_sensitivity: float = 0.5

    # Browser (Playwright)
    browser_headless: bool = True
    browser_user_agent: str | None = None

    # Brave Search (optional web search)
    brave_search_api_key: str | None = None

    # Audio
    sample_rate: int = 16000
    channels: int = 1
    vad_aggressiveness: int = 2
    stop_ms: int = 700
    tail_ms: int = 100
    audio_input_device: int | str | None = None

    # Logging (MIRA-prefixed to avoid colliding with generic env)
    log_level: str = Field(default="INFO", validation_alias="MIRA_LOG_LEVEL")
    log_json: bool = Field(default=False, validation_alias="MIRA_LOG_JSON")

    # Telemetry / debug dashboard. Bound to loopback only; no auth needed
    # because the socket isn't reachable off-host. `events_persist` is the
    # master switch for SQLite event recording — flip off in CI / tests
    # that don't want disk IO on every log_event.
    dashboard_enabled: bool = Field(
        default=True, validation_alias="MIRA_DASHBOARD_ENABLED"
    )
    dashboard_port: int = Field(
        default=17650, validation_alias="MIRA_DASHBOARD_PORT"
    )
    events_persist: bool = Field(
        default=True, validation_alias="MIRA_EVENTS_PERSIST"
    )

    # UI bridge — WebSocket server the SwiftUI HUD connects to. Loopback,
    # no auth; same trust model as the dashboard. Port is pinned separately
    # from the dashboard so both can run concurrently.
    ui_bridge_enabled: bool = Field(
        default=True, validation_alias="MIRA_UI_BRIDGE_ENABLED"
    )
    ui_bridge_port: int = Field(
        default=17651, validation_alias="MIRA_UI_BRIDGE_PORT"
    )

    def missing_required_keys(self) -> list[str]:
        required: dict[str, str | None] = {
            "CARTESIA_API_KEY": self.cartesia_api_key,
            "CARTESIA_VOICE": self.cartesia_voice,
        }
        # Planner key: OpenAI or DeepSeek is acceptable. Either covers the
        # Supervisor/planner path (DeepSeek is OpenAI-compatible; the LLM
        # gateway dispatches on model prefix). Anthropic alone is not —
        # we don't route the streaming hot path through it yet.
        if not self.openai_api_key and not self.deepseek_api_key:
            required["OPENAI_API_KEY or DEEPSEEK_API_KEY"] = None
        # STT: Deepgram is the streaming primary, OpenAI Whisper is the
        # fallback. At least one must be present or we have no way to
        # transcribe speech.
        if not self.deepgram_api_key and not self.openai_api_key:
            required["DEEPGRAM_API_KEY or OPENAI_API_KEY"] = None
        # Picovoice is only required when Porcupine is actually going to run.
        # Under "auto" with no key, the factory falls back to openwakeword —
        # no reason to refuse boot. Explicit "porcupine" without a key is
        # still a misconfig we should surface.
        backend = (self.wakeword_backend or "auto").lower()
        if backend == "porcupine" or (backend == "auto" and self.picovoice_access_key):
            required["PICOVOICE_ACCESS_KEY"] = self.picovoice_access_key
        return [name for name, val in required.items() if not val]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
