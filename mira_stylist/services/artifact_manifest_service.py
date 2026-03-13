from __future__ import annotations

import mimetypes
from pathlib import Path

from mira_stylist.models import (
    ClothAlignmentArtifact,
    GarmentItem,
    GarmentSegmentationArtifact,
    HumanParsingArtifact,
    ImageArtifact,
    PoseArtifact,
    TryOnArtifactManifest,
    TryOnRequest,
    UserAvatar,
    VTONInputPayload,
)
from mira_stylist.models.common import utc_now
from mira_stylist.utils.ids import new_prefixed_id
from mira_stylist.utils.paths import TryOnStoragePaths

from .storage_service import AssetStorageService


class ArtifactManifestService:
    """Translate resolved preprocessing artifacts into a persisted try-on manifest."""

    def __init__(self, storage: AssetStorageService):
        self.storage = storage

    def create_manifest(
        self,
        *,
        job_id: str,
        request: TryOnRequest,
        avatar: UserAvatar,
        garment: GarmentItem,
        payload: VTONInputPayload | None,
        storage_paths: TryOnStoragePaths,
    ) -> TryOnArtifactManifest:
        manifest = TryOnArtifactManifest(
            manifest_id=new_prefixed_id("manifest"),
            job_id=job_id,
            avatar_id=avatar.avatar_id,
            garment_id=garment.garment_id,
            user_image=self._image_artifact(
                role="user_image",
                path=self._resolve_avatar_image(avatar),
                notes=["Primary image used for try-on conditioning."],
            ),
            garment_image=self._image_artifact(
                role="garment_image",
                path=self._resolve_garment_image(garment),
                notes=["Canonical garment image selected for try-on conditioning."],
            ),
            provider_hint="remote_gpu_api",
            notes=[
                "Artifact manifest created for a production-style staged try-on pipeline.",
                "This manifest is intended to become the stable backend-to-GPU-worker contract.",
            ],
        )
        if payload is not None:
            manifest.provider_hint = "vton_provider"
            manifest.pose = PoseArtifact(
                provider="pose_estimation",
                status="ok" if payload.pose_metadata_path else "unavailable",
                metadata_path=payload.pose_metadata_path,
                notes=["Pose metadata prepared for downstream alignment and VTON conditioning."],
            )
            manifest.human_parsing = HumanParsingArtifact(
                provider="human_parsing",
                status="ok" if payload.person_segmentation_path else "unavailable",
                mask_path=payload.person_segmentation_path,
                metadata_path=payload.person_segmentation_metadata_path,
                mask_type=self._mask_type_from_payload(payload.notes),
                notes=["Human parsing / agnostic mask artifact prepared for VTON input."],
            )
            garment_seg_path = self._garment_segmentation_metadata_path(payload)
            manifest.garment_segmentation = GarmentSegmentationArtifact(
                provider="garment_segmentation",
                status="ok" if payload.garment_mask_path else "unavailable",
                mask_path=payload.garment_mask_path,
                alpha_png_path=self._alpha_png_for_mask(payload.garment_mask_path),
                metadata_path=garment_seg_path,
                notes=["Garment segmentation artifact prepared for VTON input."],
            )
            manifest.cloth_alignment = ClothAlignmentArtifact(
                provider="pending_remote_worker",
                status="not_run",
                notes=["Explicit cloth alignment is not implemented yet and is expected to run on the GPU worker."],
            )
        manifest.updated_at = utc_now()
        self.storage.write_metadata(storage_paths.metadata_dir / "artifact_manifest.json", manifest)
        return manifest

    @staticmethod
    def _mask_type_from_payload(notes: list[str]) -> str | None:
        for note in notes:
            if "mask_type=" in note:
                return note.split("mask_type=", 1)[1].split(".", 1)[0].strip()
        return None

    @staticmethod
    def _garment_segmentation_metadata_path(payload: VTONInputPayload) -> str | None:
        if not payload.garment_mask_path:
            return None
        candidate = Path(payload.garment_mask_path).with_name("garment_segmentation.json")
        return str(candidate) if candidate.exists() else None

    @staticmethod
    def _alpha_png_for_mask(mask_path: str | None) -> str | None:
        if not mask_path:
            return None
        candidate = Path(mask_path).with_name("garment_alpha.png")
        return str(candidate) if candidate.exists() else None

    @staticmethod
    def _resolve_avatar_image(avatar: UserAvatar) -> str:
        return (
            avatar.assets.front_capture_path
            or avatar.assets.preview_image_path
            or avatar.assets.side_preview_image_path
            or ""
        )

    @staticmethod
    def _resolve_garment_image(garment: GarmentItem) -> str:
        return garment.assets.primary_image_path or garment.assets.segmented_asset_path or ""

    def _image_artifact(self, *, role: str, path: str, notes: list[str]) -> ImageArtifact:
        file_path = Path(path) if path else None
        mime_type = mimetypes.guess_type(path)[0] if path else None
        width = height = None
        if file_path and file_path.exists() and file_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            try:
                from PIL import Image

                with Image.open(file_path) as image:
                    width, height = image.size
            except Exception:
                width = height = None
        return ImageArtifact(
            role=role,
            path=path,
            mime_type=mime_type,
            width=width,
            height=height,
            notes=notes,
        )
