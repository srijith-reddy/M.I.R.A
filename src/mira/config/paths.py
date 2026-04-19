from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import platformdirs

APP_NAME = "MIRA"


def _env_path(key: str, default: Path) -> Path:
    raw = os.getenv(key)
    return Path(raw).expanduser().resolve() if raw else default


@dataclass(frozen=True)
class Paths:
    data_dir: Path
    cache_dir: Path
    config_dir: Path
    logs_dir: Path
    browser_profile: Path
    downloads: Path
    sqlite_db: Path
    faiss_index: Path
    profile_json: Path
    gmail_credentials: Path
    gmail_token: Path

    def ensure(self) -> None:
        for p in (
            self.data_dir,
            self.cache_dir,
            self.config_dir,
            self.logs_dir,
            self.browser_profile,
            self.downloads,
        ):
            p.mkdir(parents=True, exist_ok=True)


def _build() -> Paths:
    data_dir = _env_path("MIRA_DATA_DIR", Path(platformdirs.user_data_dir(APP_NAME)))
    cache_dir = _env_path("MIRA_CACHE_DIR", Path(platformdirs.user_cache_dir(APP_NAME)))
    config_dir = _env_path("MIRA_CONFIG_DIR", Path(platformdirs.user_config_dir(APP_NAME)))

    return Paths(
        data_dir=data_dir,
        cache_dir=cache_dir,
        config_dir=config_dir,
        logs_dir=cache_dir / "logs",
        browser_profile=data_dir / "browser-profile",
        downloads=data_dir / "downloads",
        sqlite_db=data_dir / "memory.db",
        faiss_index=data_dir / "episodic.faiss",
        profile_json=config_dir / "profile.json",
        gmail_credentials=data_dir / "gmail_credentials.json",
        gmail_token=data_dir / "gmail_token.json",
    )


paths: Paths = _build()
