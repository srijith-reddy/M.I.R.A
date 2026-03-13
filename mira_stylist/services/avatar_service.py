from __future__ import annotations

import base64
import binascii
import mimetypes
from pathlib import Path

from mira_stylist.models import AvatarPhotoCaptureRequest, CreateAvatarRequest, QuickTryOnAvatarRequest, ScanBetaBuildRequest, UserAvatar
from mira_stylist.models.avatar import AvatarStatus, BodyMeasurements
from mira_stylist.models.common import SourceType, utc_now
from mira_stylist.pipelines.avatar_building import AvatarBuildingPipeline, AvatarPhotoCapture
from mira_stylist.utils import inspect_image_bytes, sanitize_filename, sha256_bytes
from mira_stylist.utils.ids import new_prefixed_id
from mira_stylist.utils.paths import AvatarStoragePaths, avatar_storage_paths

from .scan_session_service import ScanSessionService
from .apple_vision_service import AppleVisionService
from .storage_service import AssetStorageService


class AvatarService:
    """Lifecycle service for building and retrieving user avatars."""

    def __init__(
        self,
        storage: AssetStorageService,
        scan_sessions: ScanSessionService,
        pipeline: AvatarBuildingPipeline | None = None,
        vision_service: AppleVisionService | None = None,
    ):
        self.storage = storage
        self.scan_sessions = scan_sessions
        self.pipeline = pipeline or AvatarBuildingPipeline()
        self.vision_service = vision_service or AppleVisionService()
        self._avatars: dict[str, UserAvatar] = {}
        self._load_existing_avatars()

    def create_avatar(self, request: CreateAvatarRequest) -> UserAvatar:
        """
        Build or register a user avatar.

        TODO:
        - support async job execution for heavy reconstruction
        - add versioned meshes and avatar revision history
        - support reprocessing when better models become available
        """

        avatar_id = new_prefixed_id("avatar")
        scan_session = None
        if request.scan_session_id:
            scan_session = self.scan_sessions.get_scan_session(request.scan_session_id)
            if scan_session:
                self.scan_sessions.mark_processing(request.scan_session_id)

        storage_paths = self.storage.ensure_avatar_paths(request.user_id, avatar_id)
        artifacts = self.pipeline.build_avatar(
            scan_session=scan_session,
            storage_paths=storage_paths,
            measurements_override=request.measurements_override,
        )

        avatar = UserAvatar(
            user_id=request.user_id,
            avatar_id=avatar_id,
            display_name=request.display_name,
            status=AvatarStatus.READY,
            source_type=request.source_type,
            scan_session_id=request.scan_session_id,
            measurements=artifacts.measurements,
            body_profile=artifacts.body_profile,
            assets=artifacts.assets,
        )
        avatar.updated_at = utc_now()

        self.storage.write_metadata(storage_paths.metadata_dir / "avatar.json", avatar)
        self.storage.write_metadata(storage_paths.metadata_dir / "build_notes.json", {"notes": artifacts.notes})
        if scan_session:
            self.scan_sessions.mark_ready(scan_session.scan_session_id)
        self._avatars[avatar_id] = avatar
        return avatar

    def create_avatar_from_photos(self, request: AvatarPhotoCaptureRequest) -> UserAvatar:
        avatar_id = new_prefixed_id("avatar")
        storage_paths = self.storage.ensure_avatar_paths(request.user_id, avatar_id)
        photo_capture, measurements_override = self._persist_photo_captures(request, storage_paths)
        vision_analyses = self._persist_vision_analysis(photo_capture, storage_paths)
        artifacts = self.pipeline.build_avatar(
            scan_session=None,
            storage_paths=storage_paths,
            measurements_override=measurements_override,
            photo_capture=photo_capture,
        )
        self._annotate_avatar_with_vision(artifacts.body_profile, vision_analyses, is_quick_tryon=False)
        avatar = UserAvatar(
            user_id=request.user_id,
            avatar_id=avatar_id,
            display_name=request.display_name,
            status=AvatarStatus.READY,
            source_type=SourceType.IMAGE_ESTIMATED,
            measurements=artifacts.measurements,
            body_profile=artifacts.body_profile,
            assets=artifacts.assets,
        )
        avatar.updated_at = utc_now()

        self.storage.write_metadata(storage_paths.metadata_dir / "avatar.json", avatar)
        self.storage.write_metadata(storage_paths.metadata_dir / "build_notes.json", {"notes": artifacts.notes})
        self._avatars[avatar_id] = avatar
        return avatar

    def create_avatar_from_quick_photo(self, request: QuickTryOnAvatarRequest) -> UserAvatar:
        avatar_id = new_prefixed_id("avatar")
        storage_paths = self.storage.ensure_avatar_paths(request.user_id, avatar_id)
        photo_capture, measurements_override = self._persist_single_photo_capture(request, storage_paths)
        vision_analyses = self._persist_vision_analysis(photo_capture, storage_paths)
        artifacts = self.pipeline.build_avatar(
            scan_session=None,
            storage_paths=storage_paths,
            measurements_override=measurements_override,
            photo_capture=photo_capture,
        )
        self._annotate_avatar_with_vision(artifacts.body_profile, vision_analyses, is_quick_tryon=True)
        artifacts.body_profile.profile_confidence = round(min(artifacts.body_profile.profile_confidence, 0.52), 2)
        artifacts.body_profile.posture_hint = "single_photo"
        artifacts.body_profile.notes.append("Quick Try-On uses one image only and should be treated as a low-confidence fit preview.")

        avatar = UserAvatar(
            user_id=request.user_id,
            avatar_id=avatar_id,
            display_name=request.display_name,
            status=AvatarStatus.READY,
            source_type=SourceType.IMAGE_ESTIMATED,
            measurements=artifacts.measurements,
            body_profile=artifacts.body_profile,
            assets=artifacts.assets,
        )
        avatar.updated_at = utc_now()

        self.storage.write_metadata(storage_paths.metadata_dir / "avatar.json", avatar)
        self.storage.write_metadata(storage_paths.metadata_dir / "build_notes.json", {"notes": artifacts.notes})
        self.storage.write_metadata(storage_paths.metadata_dir / "body_profile.json", avatar.body_profile)
        self._avatars[avatar_id] = avatar
        return avatar

    def create_avatar_from_scan_beta(self, request: ScanBetaBuildRequest) -> UserAvatar:
        scan_session = self.scan_sessions.get_scan_session(request.scan_session_id)
        if not scan_session:
            raise ValueError("Scan session not found.")
        capture_bundle = self.scan_sessions.get_capture_bundle(request.scan_session_id)
        if not capture_bundle:
            raise ValueError("No scan capture bundle has been registered for this session.")

        avatar_id = new_prefixed_id("avatar")
        storage_paths = self.storage.ensure_avatar_paths(scan_session.user_id, avatar_id)
        self.scan_sessions.mark_processing(scan_session.scan_session_id)

        measurements = self._measurements_from_scan_bundle(
            request=request,
            frame_count=max(scan_session.frame_count, capture_bundle.rgb_frame_count),
            depth_frame_count=max(scan_session.depth_frame_count, capture_bundle.depth_frame_count),
            coverage_score=max(scan_session.quality_score, capture_bundle.coverage_score),
        )
        artifacts = self.pipeline.build_avatar(
            scan_session=scan_session,
            storage_paths=storage_paths,
            measurements_override=measurements,
        )
        artifacts.body_profile.profile_confidence = round(min(max(capture_bundle.coverage_score, scan_session.quality_score) * 0.82, 0.78), 2)
        artifacts.body_profile.posture_hint = "scan_beta"
        artifacts.body_profile.notes.append("Derived from scan-session metadata and coverage heuristics. Fused body reconstruction is not implemented yet.")

        avatar = UserAvatar(
            user_id=scan_session.user_id,
            avatar_id=avatar_id,
            display_name=request.display_name or scan_session.user_id,
            status=AvatarStatus.READY,
            source_type=scan_session.source_type,
            scan_session_id=scan_session.scan_session_id,
            measurements=artifacts.measurements,
            body_profile=artifacts.body_profile,
            assets=artifacts.assets,
        )
        avatar.updated_at = utc_now()

        self.storage.write_metadata(storage_paths.metadata_dir / "avatar.json", avatar)
        self.storage.write_metadata(storage_paths.metadata_dir / "build_notes.json", {"notes": artifacts.notes})
        self.storage.write_metadata(storage_paths.metadata_dir / "scan_beta_bundle.json", capture_bundle)
        self.storage.write_metadata(storage_paths.metadata_dir / "body_profile.json", avatar.body_profile)
        self.scan_sessions.mark_ready(scan_session.scan_session_id)
        self._avatars[avatar_id] = avatar
        return avatar

    def get_avatar(self, avatar_id: str) -> UserAvatar | None:
        avatar = self._avatars.get(avatar_id)
        if avatar:
            return avatar
        return self._load_avatar_from_disk(avatar_id)

    def ensure_vision_assets(self, avatar: UserAvatar) -> UserAvatar:
        if not avatar.assets.front_capture_path:
            return avatar
        storage_paths = avatar_storage_paths(self.storage.settings.storage_root, avatar.user_id, avatar.avatar_id)
        metadata_dir = storage_paths.metadata_dir
        needs_front_pose = not (metadata_dir / "vision_front.json").exists()
        needs_front_segmentation = not (metadata_dir / "segmentation_front.json").exists()
        needs_side_pose = bool(avatar.assets.side_capture_path) and not (metadata_dir / "vision_side.json").exists()
        needs_side_segmentation = bool(avatar.assets.side_capture_path) and not (metadata_dir / "segmentation_side.json").exists()
        if not any([needs_front_pose, needs_front_segmentation, needs_side_pose, needs_side_segmentation]):
            return avatar
        photo_capture = self._load_existing_photo_capture(avatar, storage_paths)
        if photo_capture is None:
            return avatar
        analyses = self._persist_vision_analysis(photo_capture, storage_paths)
        if analyses:
            self._annotate_avatar_with_vision(avatar.body_profile, analyses, is_quick_tryon=avatar.body_profile.posture_hint == "single_photo")
            avatar.updated_at = utc_now()
            self.storage.write_metadata(storage_paths.metadata_dir / "body_profile.json", avatar.body_profile)
            self.storage.write_metadata(storage_paths.metadata_dir / "avatar.json", avatar)
            self._avatars[avatar.avatar_id] = avatar
        return avatar

    def _load_existing_avatars(self) -> None:
        for path in self.storage.glob("avatars/*/*/metadata/avatar.json"):
            avatar = self.storage.read_model(path, UserAvatar)
            if avatar:
                self._avatars[avatar.avatar_id] = avatar

    def _load_avatar_from_disk(self, avatar_id: str) -> UserAvatar | None:
        matches = self.storage.glob(f"avatars/*/{avatar_id}/metadata/avatar.json")
        if not matches:
            return None
        avatar = self.storage.read_model(matches[0], UserAvatar)
        if avatar:
            self._avatars[avatar.avatar_id] = avatar
        return avatar

    def _persist_photo_captures(
        self,
        request: AvatarPhotoCaptureRequest,
        storage_paths: AvatarStoragePaths,
    ) -> tuple[AvatarPhotoCapture, BodyMeasurements]:
        front_bytes = self._decode_base64_payload(request.front_image_base64)
        side_bytes = self._decode_base64_payload(request.side_image_base64)

        front_mime, front_width, front_height = inspect_image_bytes(front_bytes)
        side_mime, side_width, side_height = inspect_image_bytes(side_bytes)
        resolved_front_mime = request.front_mime_type or front_mime
        resolved_side_mime = request.side_mime_type or side_mime

        front_name = sanitize_filename(request.front_original_filename, fallback_stem="front_capture")
        side_name = sanitize_filename(request.side_original_filename, fallback_stem="side_capture")
        front_suffix = Path(front_name).suffix or mimetypes.guess_extension(resolved_front_mime or "") or ".bin"
        side_suffix = Path(side_name).suffix or mimetypes.guess_extension(resolved_side_mime or "") or ".bin"
        front_path = storage_paths.captures_dir / f"{Path(front_name).stem}{front_suffix}"
        side_path = storage_paths.captures_dir / f"{Path(side_name).stem}{side_suffix}"

        self.storage.write_binary(front_path, front_bytes)
        self.storage.write_binary(side_path, side_bytes)
        self.storage.write_metadata(
            storage_paths.metadata_dir / "photo_capture.json",
            {
                "front_capture": {
                    "path": str(front_path),
                    "mime_type": resolved_front_mime,
                    "width": front_width,
                    "height": front_height,
                    "sha256": sha256_bytes(front_bytes),
                },
                "side_capture": {
                    "path": str(side_path),
                    "mime_type": resolved_side_mime,
                    "width": side_width,
                    "height": side_height,
                    "sha256": sha256_bytes(side_bytes),
                },
                "notes": request.notes,
            },
        )

        if request.measurements_hint is not None:
            if hasattr(request.measurements_hint, "model_copy"):
                measurements = request.measurements_hint.model_copy(deep=True)
            else:
                measurements = request.measurements_hint.copy(deep=True)
        else:
            measurements = None
        height_cm = request.height_cm or (measurements.height_cm if measurements else None) or 170.0
        if measurements is not None:
            if measurements.height_cm is None and request.height_cm is not None:
                measurements.height_cm = request.height_cm
            measurements.notes = measurements.notes or "Measurements supplied by user and paired with photo captures."
        else:
            front_ratio = (front_width or 1) / max(front_height or 1, 1)
            side_ratio = (side_width or 1) / max(side_height or 1, 1)
            shoulder_width_cm = round(max(38.0, min(54.0, height_cm * (0.23 + front_ratio * 0.05))), 1)
            chest_cm = round(max(84.0, min(118.0, height_cm * (0.53 + front_ratio * 0.08))), 1)
            waist_cm = round(max(68.0, min(108.0, chest_cm * (0.82 + min(front_ratio, 0.6) * 0.08))), 1)
            hips_cm = round(max(86.0, min(120.0, waist_cm * (1.06 + side_ratio * 0.1))), 1)
            inseam_cm = round(max(70.0, min(92.0, height_cm * (0.45 + side_ratio * 0.05))), 1)
            measurements = BodyMeasurements(
                height_cm=height_cm,
                chest_cm=chest_cm,
                waist_cm=waist_cm,
                hips_cm=hips_cm,
                inseam_cm=inseam_cm,
                shoulder_width_cm=shoulder_width_cm,
                body_shape_confidence=0.48,
                notes="Measurements inferred from front/side capture framing heuristics, not landmark extraction.",
            )

        return (
            AvatarPhotoCapture(
                front_path=str(front_path),
                side_path=str(side_path),
                front_mime_type=resolved_front_mime,
                side_mime_type=resolved_side_mime,
                front_width=front_width,
                front_height=front_height,
                side_width=side_width,
                side_height=side_height,
                notes=request.notes,
            ),
            measurements,
        )

    def _persist_single_photo_capture(
        self,
        request: QuickTryOnAvatarRequest,
        storage_paths: AvatarStoragePaths,
    ) -> tuple[AvatarPhotoCapture, BodyMeasurements]:
        image_bytes = self._decode_base64_payload(request.image_base64)
        image_mime, image_width, image_height = inspect_image_bytes(image_bytes)
        resolved_mime = request.mime_type or image_mime
        image_name = sanitize_filename(request.original_filename, fallback_stem="quick_tryon")
        suffix = Path(image_name).suffix or mimetypes.guess_extension(resolved_mime or "") or ".bin"
        image_path = storage_paths.captures_dir / f"{Path(image_name).stem}{suffix}"
        self.storage.write_binary(image_path, image_bytes)
        self.storage.write_metadata(
            storage_paths.metadata_dir / "quick_tryon_capture.json",
            {
                "path": str(image_path),
                "mime_type": resolved_mime,
                "width": image_width,
                "height": image_height,
                "sha256": sha256_bytes(image_bytes),
                "notes": request.notes,
            },
        )

        height_cm = request.height_cm or 170.0
        image_ratio = (image_width or 1) / max(image_height or 1, 1)
        measurements = BodyMeasurements(
            height_cm=height_cm,
            chest_cm=round(max(84.0, min(114.0, height_cm * (0.52 + image_ratio * 0.06))), 1),
            waist_cm=round(max(68.0, min(104.0, height_cm * (0.43 + min(image_ratio, 0.58) * 0.05))), 1),
            hips_cm=round(max(84.0, min(116.0, height_cm * (0.51 + image_ratio * 0.05))), 1),
            inseam_cm=round(max(70.0, min(90.0, height_cm * 0.45)), 1),
            shoulder_width_cm=round(max(38.0, min(53.0, height_cm * (0.225 + image_ratio * 0.03))), 1),
            body_shape_confidence=0.34,
            notes="Measurements inferred from a single quick-try-on photo. Use Guided Capture or Scan Beta for better confidence.",
        )

        return (
            AvatarPhotoCapture(
                front_path=str(image_path),
                side_path=None,
                front_mime_type=resolved_mime,
                side_mime_type=None,
                front_width=image_width,
                front_height=image_height,
                side_width=None,
                side_height=None,
                notes=request.notes,
            ),
            measurements,
        )

    def _persist_vision_analysis(
        self,
        photo_capture: AvatarPhotoCapture,
        storage_paths: AvatarStoragePaths,
    ) -> dict[str, object]:
        analyses: dict[str, object] = {}
        capture_map = {
            "front": photo_capture.front_path,
            "side": photo_capture.side_path,
        }
        for view, capture_path in capture_map.items():
            if not capture_path:
                continue
            analysis = self.vision_service.analyze_body_pose(capture_path, view=view)
            if analysis is None:
                analysis = None
            if analysis is not None:
                self.storage.write_metadata(storage_paths.metadata_dir / f"vision_{view}.json", analysis)
                analyses[view] = analysis
            segmentation = self.vision_service.analyze_person_segmentation(
                capture_path,
                view=view,
                output_mask_path=storage_paths.captures_dir / f"{view}_person_mask.png",
            )
            if segmentation is not None:
                self.storage.write_metadata(storage_paths.metadata_dir / f"segmentation_{view}.json", segmentation)
                analyses[f"segmentation_{view}"] = segmentation
        return analyses

    @staticmethod
    def _annotate_avatar_with_vision(body_profile, analyses: dict[str, object], *, is_quick_tryon: bool) -> None:
        front = analyses.get("front")
        front_segmentation = analyses.get("segmentation_front")
        if not front:
            if front_segmentation and getattr(front_segmentation, "status", None) == "ok":
                body_profile.notes.append("Apple Vision person segmentation found a usable full-body silhouette for the front capture.")
            return
        status = getattr(front, "status", None)
        point_count = len(getattr(front, "points", {}) or {})
        if status == "ok" and point_count >= 4:
            body_profile.notes.append("Apple Vision body-pose detection found usable upper-body landmarks for the front capture.")
            if not is_quick_tryon:
                body_profile.profile_confidence = round(min(max(body_profile.profile_confidence, 0.64), 0.78), 2)
                if body_profile.posture_hint == "neutral":
                    body_profile.posture_hint = "vision_pose"
        if front_segmentation and getattr(front_segmentation, "status", None) == "ok":
            body_profile.notes.append("Apple Vision person segmentation found a usable full-body silhouette for the front capture.")
            if not is_quick_tryon:
                body_profile.profile_confidence = round(min(max(body_profile.profile_confidence, 0.68), 0.82), 2)

    def _load_existing_photo_capture(
        self,
        avatar: UserAvatar,
        storage_paths: AvatarStoragePaths,
    ) -> AvatarPhotoCapture | None:
        front_path = avatar.assets.front_capture_path
        if not front_path:
            return None
        side_path = avatar.assets.side_capture_path
        quick_payload = self.storage.read_metadata(storage_paths.metadata_dir / "quick_tryon_capture.json") or {}
        photo_payload = self.storage.read_metadata(storage_paths.metadata_dir / "photo_capture.json") or {}
        front_meta = photo_payload.get("front_capture") if isinstance(photo_payload.get("front_capture"), dict) else quick_payload
        side_meta = photo_payload.get("side_capture") if isinstance(photo_payload.get("side_capture"), dict) else {}
        return AvatarPhotoCapture(
            front_path=front_path,
            side_path=side_path,
            front_mime_type=front_meta.get("mime_type"),
            side_mime_type=side_meta.get("mime_type"),
            front_width=front_meta.get("width"),
            front_height=front_meta.get("height"),
            side_width=side_meta.get("width"),
            side_height=side_meta.get("height"),
            notes=quick_payload.get("notes") or photo_payload.get("notes"),
        )

    @staticmethod
    def _measurements_from_scan_bundle(
        *,
        request: ScanBetaBuildRequest,
        frame_count: int,
        depth_frame_count: int,
        coverage_score: float,
    ) -> BodyMeasurements:
        if request.measurements_hint is not None:
            if hasattr(request.measurements_hint, "model_copy"):
                measurements = request.measurements_hint.model_copy(deep=True)
            else:
                measurements = request.measurements_hint.copy(deep=True)
            if measurements.height_cm is None and request.height_cm is not None:
                measurements.height_cm = request.height_cm
            measurements.body_shape_confidence = max(measurements.body_shape_confidence, round(min(coverage_score * 0.78, 0.72), 2))
            measurements.notes = measurements.notes or "Measurements supplied by user and paired with scan-session metadata."
            return measurements

        height_cm = request.height_cm or 170.0
        frame_signal = min(frame_count / 140.0, 1.0)
        depth_signal = min(depth_frame_count / 90.0, 1.0)
        shoulder_width_cm = round(max(39.0, min(55.0, height_cm * (0.228 + frame_signal * 0.025))), 1)
        chest_cm = round(max(86.0, min(120.0, height_cm * (0.53 + depth_signal * 0.07))), 1)
        waist_cm = round(max(68.0, min(110.0, chest_cm * (0.8 + coverage_score * 0.06))), 1)
        hips_cm = round(max(88.0, min(122.0, waist_cm * (1.07 + depth_signal * 0.05))), 1)
        inseam_cm = round(max(71.0, min(93.0, height_cm * (0.45 + depth_signal * 0.03))), 1)
        return BodyMeasurements(
            height_cm=height_cm,
            chest_cm=chest_cm,
            waist_cm=waist_cm,
            hips_cm=hips_cm,
            inseam_cm=inseam_cm,
            shoulder_width_cm=shoulder_width_cm,
            body_shape_confidence=round(min(0.42 + coverage_score * 0.32, 0.76), 2),
            notes="Measurements inferred from scan-session frame/depth coverage heuristics. Depth fusion is not implemented yet.",
        )

    @staticmethod
    def _decode_base64_payload(payload: str) -> bytes:
        try:
            if "," in payload and payload.strip().startswith("data:"):
                payload = payload.split(",", 1)[1]
            return base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Invalid base64 image payload.") from exc
