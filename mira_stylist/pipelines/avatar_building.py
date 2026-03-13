from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from mira_stylist.models.avatar import AvatarAssetManifest, BodyMeasurements, BodyProfile, ScanSession
from mira_stylist.utils.paths import AvatarStoragePaths


@dataclass(frozen=True)
class AvatarPhotoCapture:
    front_path: str
    side_path: str | None
    front_mime_type: str | None
    side_mime_type: str | None
    front_width: int | None
    front_height: int | None
    side_width: int | None
    side_height: int | None
    notes: str | None = None


@dataclass(frozen=True)
class AvatarBuildArtifacts:
    measurements: BodyMeasurements
    body_profile: BodyProfile
    assets: AvatarAssetManifest
    notes: list[str]


class AvatarBuildingPipeline:
    """Stub pipeline for avatar creation from scan or image-derived capture inputs."""

    def build_avatar(
        self,
        scan_session: ScanSession | None,
        storage_paths: AvatarStoragePaths,
        measurements_override: BodyMeasurements | None = None,
        photo_capture: AvatarPhotoCapture | None = None,
    ) -> AvatarBuildArtifacts:
        """
        Build avatar artifacts from a scan session.

        TODO:
        - ingest synchronized RGB + depth/LiDAR frames
        - run body landmark estimation and pose normalization
        - fuse depth into a canonical mesh or parametric body model
        - generate texture atlas and preview renders
        """

        measurements = measurements_override or BodyMeasurements(
            height_cm=172.0,
            chest_cm=96.0,
            waist_cm=82.0,
            hips_cm=97.0,
            inseam_cm=79.0,
            shoulder_width_cm=44.0,
            body_shape_confidence=0.35 if not scan_session else max(0.35, scan_session.quality_score * 0.7),
            notes="MVP measurements generated from scaffold heuristics, not body reconstruction.",
        )
        body_profile = self._derive_body_profile(measurements, photo_capture=photo_capture)

        assets = AvatarAssetManifest(
            mesh_path=str(storage_paths.mesh_dir / "body_mesh.glb"),
            texture_path=str(storage_paths.textures_dir / "body_texture.json"),
            preview_image_path=str(storage_paths.previews_dir / "front_preview.svg"),
            side_preview_image_path=str(storage_paths.previews_dir / "side_preview.svg"),
            skeleton_path=str(storage_paths.metadata_dir / "body_skeleton.json"),
            measurements_path=str(storage_paths.metadata_dir / "measurements.json"),
            front_capture_path=photo_capture.front_path if photo_capture else None,
            side_capture_path=photo_capture.side_path if photo_capture else None,
            body_profile_path=str(storage_paths.metadata_dir / "body_profile.json"),
            metadata_path=str(storage_paths.metadata_dir / "avatar_manifest.json"),
        )

        notes = [
            "Avatar creation is stubbed in this scaffold.",
            "Real implementation should fuse depth, RGB, and pose priors into a stable avatar.",
        ]
        if scan_session and scan_session.has_lidar:
            notes.append("LiDAR source detected; future pipeline can enable higher-fidelity reconstruction.")
        if photo_capture:
            notes.append("Front and side photo captures were used to derive a lightweight body profile.")
            notes.append("Photo-derived shape is heuristic only; no segmentation or landmark model is running.")
            if not photo_capture.side_path:
                notes.append("Only one body photo was provided, so side and depth estimates are lower confidence.")

        Path(assets.mesh_path).write_text("# placeholder avatar mesh\n", encoding="utf-8")  # type: ignore[arg-type]
        Path(assets.texture_path).write_text(
            json.dumps({"status": "placeholder", "note": "Texture atlas generation is not implemented."}, indent=2),
            encoding="utf-8",
        )  # type: ignore[arg-type]
        Path(assets.skeleton_path).write_text(
            json.dumps({"status": "placeholder", "note": "Skeleton fitting is a future advanced stage."}, indent=2),
            encoding="utf-8",
        )  # type: ignore[arg-type]
        if hasattr(measurements, "model_dump"):
            measurement_payload = measurements.model_dump()
        else:
            measurement_payload = measurements.dict()
        Path(assets.measurements_path).write_text(json.dumps(measurement_payload, indent=2), encoding="utf-8")  # type: ignore[arg-type]
        if hasattr(body_profile, "model_dump"):
            body_profile_payload = body_profile.model_dump()
        else:
            body_profile_payload = body_profile.dict()
        Path(assets.body_profile_path).write_text(json.dumps(body_profile_payload, indent=2), encoding="utf-8")  # type: ignore[arg-type]
        Path(assets.preview_image_path).write_text(
            self._preview_svg(measurements, body_profile=body_profile, view="front"),
            encoding="utf-8",
        )  # type: ignore[arg-type]
        Path(assets.side_preview_image_path).write_text(
            self._preview_svg(measurements, body_profile=body_profile, view="side"),
            encoding="utf-8",
        )  # type: ignore[arg-type]

        return AvatarBuildArtifacts(
            measurements=measurements,
            body_profile=body_profile,
            assets=assets,
            notes=notes,
        )

    @staticmethod
    def _derive_body_profile(
        measurements: BodyMeasurements,
        *,
        photo_capture: Optional[AvatarPhotoCapture],
    ) -> BodyProfile:
        height = measurements.height_cm or 170.0
        chest = measurements.chest_cm or 96.0
        waist = measurements.waist_cm or max(chest * 0.84, 72.0)
        hips = measurements.hips_cm or max(waist * 1.08, 92.0)
        shoulder = measurements.shoulder_width_cm or 43.0
        inseam = measurements.inseam_cm or (height * 0.46)

        front_ratio = 0.43
        side_ratio = 0.26
        notes = [
            "Body profile is derived from measurement hints and capture framing heuristics.",
        ]
        confidence = 0.34
        if photo_capture and photo_capture.front_width and photo_capture.front_height:
            front_ratio = photo_capture.front_width / max(photo_capture.front_height, 1)
            confidence += 0.16
        if photo_capture and photo_capture.side_width and photo_capture.side_height:
            side_ratio = photo_capture.side_width / max(photo_capture.side_height, 1)
            confidence += 0.16
        if measurements.height_cm:
            confidence += 0.1
        if measurements.chest_cm or measurements.waist_cm or measurements.hips_cm:
            confidence += 0.1
        if measurements.shoulder_width_cm or measurements.inseam_cm:
            confidence += 0.08

        shoulder_scale = _clamp((shoulder / 43.0) * (0.95 + front_ratio * 0.12), 0.82, 1.24)
        waist_scale = _clamp((waist / max(chest, 1.0)) * 1.04, 0.78, 1.12)
        hip_scale = _clamp((hips / max(chest, 1.0)) * 0.98, 0.84, 1.18)
        leg_length_ratio = _clamp(inseam / max(height, 1.0), 0.4, 0.58)
        torso_length_ratio = _clamp(0.82 - leg_length_ratio, 0.3, 0.45)
        depth_scale = _clamp((side_ratio / max(front_ratio, 0.12)) * 1.18, 0.7, 1.1)

        frame_score = (shoulder_scale + hip_scale + (1.05 - waist_scale)) / 3.0
        if frame_score >= 1.05:
            body_frame = "broad"
        elif frame_score <= 0.93:
            body_frame = "slim"
        else:
            body_frame = "regular"

        posture_hint = "neutral"
        if front_ratio > 0.55:
            posture_hint = "cropped"
            notes.append("Front capture appears tightly cropped; proportions may be less stable.")
        elif side_ratio < 0.18:
            posture_hint = "turned"
            notes.append("Side capture appears narrow; depth estimate is low confidence.")
        else:
            notes.append("Front/side captures are consistent enough for a lightweight profile pass.")

        return BodyProfile(
            shoulder_scale=round(shoulder_scale, 2),
            waist_scale=round(waist_scale, 2),
            hip_scale=round(hip_scale, 2),
            torso_length_ratio=round(torso_length_ratio, 2),
            leg_length_ratio=round(leg_length_ratio, 2),
            depth_scale=round(depth_scale, 2),
            body_frame=body_frame,
            posture_hint=posture_hint,
            profile_confidence=round(min(confidence, 0.84), 2),
            notes=notes,
        )

    @staticmethod
    def _preview_svg(measurements: BodyMeasurements, *, body_profile: BodyProfile, view: str) -> str:
        height = measurements.height_cm or 170
        waist = measurements.waist_cm or 80
        shoulder = (measurements.shoulder_width_cm or 43) * body_profile.shoulder_scale
        hips = (measurements.hips_cm or 96) * body_profile.hip_scale
        torso_top = 250
        torso_height = 360 + int(body_profile.torso_length_ratio * 420)
        torso_bottom = torso_top + torso_height
        torso_width = 120 + int(shoulder * 1.25)
        waist_width = int(torso_width * body_profile.waist_scale * (0.62 if view == "front" else 0.42))
        hip_width = int((120 + hips * 1.15) * (1.0 if view == "front" else body_profile.depth_scale * 0.62))
        arm_offset = 70 if view == "front" else 42
        leg_height = 260 + int(body_profile.leg_length_ratio * 360)
        return (
            "<svg xmlns='http://www.w3.org/2000/svg' width='720' height='1200'>"
            "<rect width='100%' height='100%' fill='#f3efe8'/>"
            "<ellipse cx='360' cy='160' rx='70' ry='90' fill='#d6c4b2'/>"
            f"<path d='M {360 - torso_width / 2:.1f} {torso_top} C {360 - torso_width / 2 + 10:.1f} 360, {360 - waist_width / 2:.1f} 520, {360 - hip_width / 2:.1f} {torso_bottom} L {360 + hip_width / 2:.1f} {torso_bottom} C {360 + waist_width / 2:.1f} 520, {360 + torso_width / 2 - 10:.1f} 360, {360 + torso_width / 2:.1f} {torso_top} Z' fill='#d6c4b2'/>"
            f"<rect x='{360 - torso_width / 2 - arm_offset:.1f}' y='320' width='44' height='280' rx='22' fill='#d6c4b2'/>"
            f"<rect x='{360 + torso_width / 2 + arm_offset - 44:.1f}' y='320' width='44' height='280' rx='22' fill='#d6c4b2'/>"
            f"<rect x='310' y='{torso_bottom - 5}' width='42' height='{leg_height}' rx='20' fill='#d6c4b2'/>"
            f"<rect x='368' y='{torso_bottom - 5}' width='42' height='{leg_height}' rx='20' fill='#d6c4b2'/>"
            f"<text x='80' y='1080' font-size='26' font-family='Arial'>height_cm={height}</text>"
            f"<text x='80' y='1120' font-size='26' font-family='Arial'>waist_cm={waist}</text>"
            f"<text x='80' y='1160' font-size='20' font-family='Arial'>profile={body_profile.body_frame} confidence={body_profile.profile_confidence:.2f} view={view}</text>"
            "</svg>"
        )


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))
