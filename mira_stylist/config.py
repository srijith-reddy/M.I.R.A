from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - optional dependency in isolated runner envs
    load_dotenv = None


if load_dotenv is not None:
    load_dotenv()


@dataclass(frozen=True)
class StylistSettings:
    """Runtime settings for the MIRA Stylist scaffold."""

    storage_root: Path
    api_title: str
    api_version: str
    max_source_images: int
    default_render_mode: str
    default_camera_angle: str
    vton_runner_command: str
    vton_timeout_seconds: int
    vton_model_path: str
    vton_device: str
    vton_dtype: str
    vton_num_inference_steps: int
    vton_guidance_scale: float
    vton_strength: float
    idm_vton_repo_path: str
    idm_vton_python_bin: str
    idm_vton_server_url: str
    idm_vton_denoise_steps: int
    idm_vton_seed: int
    idm_vton_auto_mask: bool
    idm_vton_auto_crop: bool
    remote_vton_url: str
    remote_vton_api_path: str
    remote_vton_callback_url: str
    remote_vton_mode: str
    remote_vton_poll_interval_seconds: int
    artifact_base_url: str
    public_base_url: str
    artifact_signing_secret: str
    artifact_url_ttl_seconds: int
    object_store_mode: str
    object_store_base_url: str


def get_settings() -> StylistSettings:
    """Return immutable settings for service initialization."""

    return StylistSettings(
        storage_root=Path(os.getenv("MIRA_STYLIST_STORAGE_ROOT", "output/mira_stylist")).resolve(),
        api_title=os.getenv("MIRA_STYLIST_API_TITLE", "MIRA Stylist API"),
        api_version=os.getenv("MIRA_STYLIST_API_VERSION", "0.1.0"),
        max_source_images=int(os.getenv("MIRA_STYLIST_MAX_SOURCE_IMAGES", "12")),
        default_render_mode=os.getenv("MIRA_STYLIST_DEFAULT_RENDER_MODE", "styled_overlay"),
        default_camera_angle=os.getenv("MIRA_STYLIST_DEFAULT_CAMERA_ANGLE", "front"),
        vton_runner_command=os.getenv("MIRA_STYLIST_VTON_RUNNER", "").strip(),
        vton_timeout_seconds=int(os.getenv("MIRA_STYLIST_VTON_TIMEOUT_SECONDS", "45")),
        vton_model_path=os.getenv("MIRA_STYLIST_VTON_MODEL_PATH", "").strip(),
        vton_device=os.getenv("MIRA_STYLIST_VTON_DEVICE", "auto").strip() or "auto",
        vton_dtype=os.getenv("MIRA_STYLIST_VTON_DTYPE", "float32").strip() or "float32",
        vton_num_inference_steps=int(os.getenv("MIRA_STYLIST_VTON_STEPS", "24")),
        vton_guidance_scale=float(os.getenv("MIRA_STYLIST_VTON_GUIDANCE_SCALE", "6.5")),
        vton_strength=float(os.getenv("MIRA_STYLIST_VTON_STRENGTH", "0.88")),
        idm_vton_repo_path=os.getenv("MIRA_STYLIST_IDM_VTON_REPO_PATH", "").strip(),
        idm_vton_python_bin=os.getenv("MIRA_STYLIST_IDM_VTON_PYTHON_BIN", "").strip() or "python",
        idm_vton_server_url=os.getenv("MIRA_STYLIST_IDM_VTON_SERVER_URL", "").strip(),
        idm_vton_denoise_steps=int(os.getenv("MIRA_STYLIST_IDM_VTON_DENOISE_STEPS", "30")),
        idm_vton_seed=int(os.getenv("MIRA_STYLIST_IDM_VTON_SEED", "42")),
        idm_vton_auto_mask=os.getenv("MIRA_STYLIST_IDM_VTON_AUTO_MASK", "true").strip().lower() not in {"0", "false", "no"},
        idm_vton_auto_crop=os.getenv("MIRA_STYLIST_IDM_VTON_AUTO_CROP", "false").strip().lower() in {"1", "true", "yes"},
        remote_vton_url=os.getenv("MIRA_STYLIST_REMOTE_VTON_URL", "").strip(),
        remote_vton_api_path=os.getenv("MIRA_STYLIST_REMOTE_VTON_API_PATH", "/tryon").strip() or "/tryon",
        remote_vton_callback_url=os.getenv("MIRA_STYLIST_REMOTE_VTON_CALLBACK_URL", "").strip(),
        remote_vton_mode=os.getenv("MIRA_STYLIST_REMOTE_VTON_MODE", "async").strip() or "async",
        remote_vton_poll_interval_seconds=int(os.getenv("MIRA_STYLIST_REMOTE_VTON_POLL_INTERVAL_SECONDS", "5")),
        artifact_base_url=os.getenv("MIRA_STYLIST_ARTIFACT_BASE_URL", "").strip(),
        public_base_url=os.getenv("MIRA_STYLIST_PUBLIC_BASE_URL", "").strip(),
        artifact_signing_secret=os.getenv("MIRA_STYLIST_ARTIFACT_SIGNING_SECRET", "").strip() or "dev-mira-stylist-secret",
        artifact_url_ttl_seconds=int(os.getenv("MIRA_STYLIST_ARTIFACT_URL_TTL_SECONDS", "900")),
        object_store_mode=os.getenv("MIRA_STYLIST_OBJECT_STORE_MODE", "backend_signed").strip() or "backend_signed",
        object_store_base_url=os.getenv("MIRA_STYLIST_OBJECT_STORE_BASE_URL", "").strip(),
    )
