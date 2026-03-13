from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from mira_stylist.config import StylistSettings, get_settings
from mira_stylist.models.common import model_dump_compat
from mira_stylist.utils.paths import (
    AvatarStoragePaths,
    GarmentInputStoragePaths,
    GarmentStoragePaths,
    OutfitStoragePaths,
    ScanSessionStoragePaths,
    TryOnStoragePaths,
    avatar_storage_paths,
    garment_input_storage_paths,
    garment_storage_paths,
    outfit_storage_paths,
    scan_session_storage_paths,
    tryon_storage_paths,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


class AssetStorageService:
    """Filesystem-oriented storage helper for avatar, garment, and preview artifacts."""

    def __init__(self, settings: StylistSettings | None = None):
        self.settings = settings or get_settings()
        self.settings.storage_root.mkdir(parents=True, exist_ok=True)

    def ensure_avatar_paths(self, user_id: str, avatar_id: str) -> AvatarStoragePaths:
        paths = avatar_storage_paths(self.settings.storage_root, user_id, avatar_id)
        self._ensure_dirs(
            paths.base_dir,
            paths.captures_dir,
            paths.mesh_dir,
            paths.textures_dir,
            paths.previews_dir,
            paths.metadata_dir,
        )
        return paths

    def ensure_scan_session_paths(
        self, user_id: str, scan_session_id: str
    ) -> ScanSessionStoragePaths:
        paths = scan_session_storage_paths(self.settings.storage_root, user_id, scan_session_id)
        self._ensure_dirs(paths.base_dir, paths.uploads_dir, paths.metadata_dir)
        return paths

    def ensure_garment_input_paths(self, input_id: str) -> GarmentInputStoragePaths:
        paths = garment_input_storage_paths(self.settings.storage_root, input_id)
        self._ensure_dirs(paths.base_dir, paths.raw_dir, paths.normalized_dir, paths.metadata_dir)
        return paths

    def ensure_garment_paths(self, garment_id: str) -> GarmentStoragePaths:
        paths = garment_storage_paths(self.settings.storage_root, garment_id)
        self._ensure_dirs(
            paths.base_dir,
            paths.raw_dir,
            paths.candidates_dir,
            paths.segmented_dir,
            paths.mesh_dir,
            paths.textures_dir,
            paths.metadata_dir,
        )
        return paths

    def ensure_tryon_paths(self, job_id: str) -> TryOnStoragePaths:
        paths = tryon_storage_paths(self.settings.storage_root, job_id)
        self._ensure_dirs(
            paths.base_dir,
            paths.previews_dir,
            paths.preprocessing_dir,
            paths.jobs_dir,
            paths.metadata_dir,
        )
        return paths

    def ensure_outfit_paths(self, outfit_id: str) -> OutfitStoragePaths:
        paths = outfit_storage_paths(self.settings.storage_root, outfit_id)
        self._ensure_dirs(paths.base_dir, paths.previews_dir, paths.metadata_dir)
        return paths

    def write_metadata(self, path: str | Path, payload: BaseModel | dict) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        serializable = model_dump_compat(payload) if isinstance(payload, BaseModel) else payload
        target.write_text(json.dumps(serializable, indent=2, default=str), encoding="utf-8")
        return target

    def read_metadata(self, path: str | Path) -> dict | None:
        target = Path(path)
        if not target.exists():
            return None
        return json.loads(target.read_text(encoding="utf-8"))

    def read_model(self, path: str | Path, model_type: type[ModelT]) -> ModelT | None:
        payload = self.read_metadata(path)
        if payload is None:
            return None
        if hasattr(model_type, "model_validate"):
            return model_type.model_validate(payload)  # type: ignore[return-value]
        return model_type.parse_obj(payload)  # type: ignore[return-value]

    def glob(self, pattern: str) -> list[Path]:
        return sorted(self.settings.storage_root.glob(pattern))

    def write_binary(self, path: str | Path, payload: bytes) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return target

    def write_text(self, path: str | Path, payload: str) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")
        return target

    @staticmethod
    def _ensure_dirs(*paths: Path) -> None:
        for path in paths:
            path.mkdir(parents=True, exist_ok=True)
