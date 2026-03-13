from __future__ import annotations

import json
import mimetypes
import subprocess
from pathlib import Path

from mira_stylist.models.vision import VisionBodyAnalysis, VisionSegmentationAnalysis


class AppleVisionService:
    """Optional macOS-native body-pose analyzer backed by Apple's Vision framework."""

    def __init__(self, script_path: Path | None = None, swift_bin: str = "swift", timeout_seconds: int = 12):
        self.script_path = script_path or Path(__file__).resolve().parents[1] / "native" / "apple_vision_body_pose.swift"
        self.segmentation_script_path = Path(__file__).resolve().parents[1] / "native" / "apple_vision_person_segmentation.swift"
        self.swift_bin = swift_bin
        self.timeout_seconds = timeout_seconds

    def analyze_body_pose(self, image_path: str | Path, *, view: str) -> VisionBodyAnalysis | None:
        path = self._resolve_supported_image(Path(image_path))
        if not path.exists() or not self.script_path.exists():
            return None
        try:
            completed = subprocess.run(
                [self.swift_bin, str(self.script_path), str(path)],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0 or not completed.stdout.strip():
            return None
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return None
        payload["view"] = view
        payload["image_path"] = str(path)
        if hasattr(VisionBodyAnalysis, "model_validate"):
            return VisionBodyAnalysis.model_validate(payload)
        return VisionBodyAnalysis.parse_obj(payload)

    def analyze_person_segmentation(
        self,
        image_path: str | Path,
        *,
        view: str,
        output_mask_path: str | Path,
    ) -> VisionSegmentationAnalysis | None:
        path = self._resolve_supported_image(Path(image_path))
        mask_path = Path(output_mask_path)
        if not path.exists() or not self.segmentation_script_path.exists():
            return None
        try:
            completed = subprocess.run(
                [self.swift_bin, str(self.segmentation_script_path), str(path), str(mask_path)],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0 or not completed.stdout.strip():
            return None
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return None
        payload["view"] = view
        payload["image_path"] = str(path)
        if hasattr(VisionSegmentationAnalysis, "model_validate"):
            return VisionSegmentationAnalysis.model_validate(payload)
        return VisionSegmentationAnalysis.parse_obj(payload)

    def _resolve_supported_image(self, path: Path) -> Path:
        mime = mimetypes.guess_type(str(path))[0] or ""
        if mime not in {"image/heic", "image/heif"}:
            return path
        converted = path.with_suffix(".preview.jpg")
        if converted.exists():
            return converted
        try:
            completed = subprocess.run(
                ["sips", "-s", "format", "jpeg", str(path), "--out", str(converted)],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError):
            return path
        if completed.returncode == 0 and converted.exists():
            return converted
        return path
