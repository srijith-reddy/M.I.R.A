from __future__ import annotations

import json
import mimetypes
from pathlib import Path

from mira_stylist.models import TryOnRequest, UserAvatar, VTONInputPayload
from mira_stylist.models.garment import GarmentCategory, GarmentItem
from mira_stylist.models.vision import VisionBodyAnalysis
from mira_stylist.utils.paths import TryOnStoragePaths
from mira_stylist.vision.garment_segmentation import GarmentSegmentationEngine
from mira_stylist.vision.human_segmentation import HumanSegmentationEngine
from mira_stylist.vision.pose_estimation import PoseEstimationEngine


class PreprocessingService:
    """Own pose estimation, human parsing, and garment segmentation for try-on preparation."""

    SUPPORTED_CATEGORIES = {
        GarmentCategory.TOP,
        GarmentCategory.OUTERWEAR,
        GarmentCategory.DRESS,
    }

    def __init__(
        self,
        *,
        pose_engine: PoseEstimationEngine | None = None,
        human_segmentation: HumanSegmentationEngine | None = None,
        garment_segmentation: GarmentSegmentationEngine | None = None,
    ) -> None:
        self.pose_engine = pose_engine or PoseEstimationEngine()
        self.human_segmentation = human_segmentation or HumanSegmentationEngine()
        self.garment_segmentation = garment_segmentation or GarmentSegmentationEngine()

    def build_vton_payload(
        self,
        *,
        job_id: str,
        request: TryOnRequest,
        avatar: UserAvatar,
        garment: GarmentItem,
        storage_paths: TryOnStoragePaths,
    ) -> VTONInputPayload | None:
        if garment.category not in self.SUPPORTED_CATEGORIES:
            return None
        avatar_image = self._resolve_avatar_image(avatar)
        garment_image = self._resolve_garment_image(garment)
        if avatar_image is None or garment_image is None:
            return None
        metadata_dir = self._avatar_metadata_dir(avatar)
        pose = None
        pose_path = None
        if metadata_dir is not None:
            pose_candidate = metadata_dir / f"vision_{request.camera_angle.value}.json"
            if not pose_candidate.exists():
                pose_candidate = metadata_dir / "vision_front.json"
            if pose_candidate.exists():
                pose_path = str(pose_candidate)
                try:
                    pose_payload = json.loads(pose_candidate.read_text(encoding="utf-8"))
                    if pose_payload:
                        if hasattr(VisionBodyAnalysis, "model_validate"):
                            pose = VisionBodyAnalysis.model_validate(pose_payload)
                        else:
                            pose = VisionBodyAnalysis.parse_obj(pose_payload)
                except (OSError, json.JSONDecodeError, ValueError):
                    pose = None
        if pose is None:
            pose = self.pose_engine.estimate(avatar_image, view=request.camera_angle.value)
            pose_path = str(storage_paths.preprocessing_dir / "pose_vton.json")
            storage_paths.preprocessing_dir.mkdir(parents=True, exist_ok=True)
            Path(pose_path).write_text(json.dumps(self._dump_model(pose), indent=2, default=str), encoding="utf-8")

        mask_type = self._mask_type_for_category(garment.category)
        segmentation = self.human_segmentation.segment(
            avatar_image,
            view=request.camera_angle.value,
            output_mask_path=storage_paths.preprocessing_dir / "person_agnostic_mask.png",
            mask_type=mask_type,
            pose_analysis=pose,
        )
        segmentation_meta = str(storage_paths.preprocessing_dir / "human_segmentation_vton.json")
        Path(segmentation_meta).write_text(json.dumps(self._dump_model(segmentation), indent=2, default=str), encoding="utf-8")
        segmentation_mask = segmentation.mask_path

        garment_seg = self.garment_segmentation.segment(
            garment_image,
            output_dir=storage_paths.preprocessing_dir / "garment_segmentation",
            category_hint=garment.category.value,
            text_prompt=garment.title,
        )
        garment_mask = garment_seg.mask_path or garment.assets.segmented_asset_path
        return VTONInputPayload(
            request_id=job_id,
            avatar_id=avatar.avatar_id,
            garment_id=garment.garment_id,
            pose=request.pose,
            camera_angle=request.camera_angle.value,
            avatar_image_path=str(avatar_image),
            garment_image_path=str(garment_image),
            person_segmentation_path=segmentation_mask,
            person_segmentation_metadata_path=segmentation_meta,
            pose_metadata_path=pose_path,
            garment_mask_path=garment_mask,
            garment_category=garment.category.value,
            garment_color=garment.color,
            garment_title=garment.title,
            output_dir=str(storage_paths.previews_dir / "vton"),
            notes=[
                "VTON adapter payload built from persisted avatar capture, pose metadata, segmentation metadata, and garment assets.",
                f"Human mask prepared with backend={segmentation.provider} and mask_type={mask_type}.",
                f"Garment mask prepared with backend={garment_seg.provider}.",
                "This path expects an external learned runner to synthesize the preview.",
            ],
        )

    @staticmethod
    def _mask_type_for_category(category: GarmentCategory) -> str:
        if category in {GarmentCategory.TOP, GarmentCategory.OUTERWEAR}:
            return "upper"
        if category in {GarmentCategory.BOTTOM, GarmentCategory.FOOTWEAR}:
            return "lower"
        if category == GarmentCategory.DRESS:
            return "overall"
        return "overall"

    @staticmethod
    def _avatar_metadata_dir(avatar: UserAvatar) -> Path | None:
        if avatar.assets.body_profile_path:
            return Path(avatar.assets.body_profile_path).parent
        if avatar.assets.metadata_path:
            return Path(avatar.assets.metadata_path).parent
        return None

    def _resolve_avatar_image(self, avatar: UserAvatar) -> Path | None:
        for raw in [avatar.assets.front_capture_path, avatar.assets.side_capture_path]:
            if not raw:
                continue
            candidate = self._resolve_browser_image(Path(raw))
            if candidate and candidate.exists():
                return candidate
        return None

    def _resolve_garment_image(self, garment: GarmentItem) -> Path | None:
        for source in garment.source_images:
            if source.local_path:
                candidate = self._resolve_browser_image(Path(source.local_path))
                if candidate and candidate.exists():
                    return candidate
        if garment.primary_image_path:
            candidate = self._resolve_browser_image(Path(garment.primary_image_path))
            if candidate and candidate.exists():
                return candidate
        return None

    @staticmethod
    def _resolve_browser_image(path: Path) -> Path | None:
        if not path.exists():
            return None
        mime = mimetypes.guess_type(str(path))[0] or ""
        if mime in {"image/heic", "image/heif"}:
            converted = path.with_suffix(".preview.jpg")
            if converted.exists():
                return converted
            return None
        return path

    @staticmethod
    def _dump_model(model) -> dict:
        if hasattr(model, "model_dump"):
            return model.model_dump()
        return model.dict()
