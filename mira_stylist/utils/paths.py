from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AvatarStoragePaths:
    base_dir: Path
    captures_dir: Path
    mesh_dir: Path
    textures_dir: Path
    previews_dir: Path
    metadata_dir: Path


@dataclass(frozen=True)
class ScanSessionStoragePaths:
    base_dir: Path
    uploads_dir: Path
    metadata_dir: Path


@dataclass(frozen=True)
class GarmentInputStoragePaths:
    base_dir: Path
    raw_dir: Path
    normalized_dir: Path
    metadata_dir: Path


@dataclass(frozen=True)
class GarmentStoragePaths:
    base_dir: Path
    raw_dir: Path
    candidates_dir: Path
    segmented_dir: Path
    mesh_dir: Path
    textures_dir: Path
    metadata_dir: Path


@dataclass(frozen=True)
class TryOnStoragePaths:
    base_dir: Path
    previews_dir: Path
    preprocessing_dir: Path
    jobs_dir: Path
    metadata_dir: Path


@dataclass(frozen=True)
class OutfitStoragePaths:
    base_dir: Path
    previews_dir: Path
    metadata_dir: Path


def avatar_storage_paths(root: Path, user_id: str, avatar_id: str) -> AvatarStoragePaths:
    base = root / "avatars" / user_id / avatar_id
    return AvatarStoragePaths(
        base_dir=base,
        captures_dir=base / "captures",
        mesh_dir=base / "mesh",
        textures_dir=base / "textures",
        previews_dir=base / "previews",
        metadata_dir=base / "metadata",
    )


def scan_session_storage_paths(
    root: Path, user_id: str, scan_session_id: str
) -> ScanSessionStoragePaths:
    base = root / "scan_sessions" / user_id / scan_session_id
    return ScanSessionStoragePaths(
        base_dir=base,
        uploads_dir=base / "uploads",
        metadata_dir=base / "metadata",
    )


def garment_input_storage_paths(root: Path, input_id: str) -> GarmentInputStoragePaths:
    base = root / "garment_inputs" / input_id
    return GarmentInputStoragePaths(
        base_dir=base,
        raw_dir=base / "raw",
        normalized_dir=base / "normalized",
        metadata_dir=base / "metadata",
    )


def garment_storage_paths(root: Path, garment_id: str) -> GarmentStoragePaths:
    base = root / "garments" / garment_id
    return GarmentStoragePaths(
        base_dir=base,
        raw_dir=base / "raw",
        candidates_dir=base / "candidates",
        segmented_dir=base / "segmented",
        mesh_dir=base / "mesh",
        textures_dir=base / "textures",
        metadata_dir=base / "metadata",
    )


def tryon_storage_paths(root: Path, job_id: str) -> TryOnStoragePaths:
    base = root / "tryon" / job_id
    return TryOnStoragePaths(
        base_dir=base,
        previews_dir=base / "previews",
        preprocessing_dir=base / "preprocessing",
        jobs_dir=base / "jobs",
        metadata_dir=base / "metadata",
    )


def outfit_storage_paths(root: Path, outfit_id: str) -> OutfitStoragePaths:
    base = root / "outfits" / outfit_id
    return OutfitStoragePaths(
        base_dir=base,
        previews_dir=base / "previews",
        metadata_dir=base / "metadata",
    )
