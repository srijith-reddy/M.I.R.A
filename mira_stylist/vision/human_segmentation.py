from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from mira_stylist.models.vision import VisionBodyAnalysis
from mira_stylist.models.vision import VisionSegmentationAnalysis

if TYPE_CHECKING:
    from mira_stylist.services.apple_vision_service import AppleVisionService


@dataclass(frozen=True)
class HumanSegmentationConfig:
    backend: str = field(
        default_factory=lambda: os.getenv("MIRA_STYLIST_HUMAN_SEGMENTATION_BACKEND", "apple_vision").strip() or "apple_vision"
    )
    runner_command: str = field(default_factory=lambda: os.getenv("MIRA_STYLIST_HUMAN_SEGMENTATION_RUNNER", "").strip())
    timeout_seconds: int = field(default_factory=lambda: int(os.getenv("MIRA_STYLIST_HUMAN_SEGMENTATION_TIMEOUT_SECONDS", "45")))


class HumanSegmentationEngine:
    """Generate person/body masks for try-on from lightweight or external backends."""

    def __init__(
        self,
        config: HumanSegmentationConfig | None = None,
        apple_vision: "AppleVisionService | None" = None,
    ) -> None:
        self.config = config or HumanSegmentationConfig()
        if apple_vision is None:
            from mira_stylist.services.apple_vision_service import AppleVisionService

            apple_vision = AppleVisionService(timeout_seconds=min(self.config.timeout_seconds, 12))
        self.apple_vision = apple_vision

    def segment(
        self,
        image_path: str | Path,
        *,
        view: str = "front",
        output_mask_path: str | Path,
        mask_type: str | None = None,
        pose_analysis: VisionBodyAnalysis | None = None,
    ) -> VisionSegmentationAnalysis:
        output_mask_path = Path(output_mask_path)
        output_mask_path.parent.mkdir(parents=True, exist_ok=True)
        backend = self.config.backend.lower()
        if backend == "apple_vision":
            analysis = self.apple_vision.analyze_person_segmentation(
                image_path=image_path,
                view=view,
                output_mask_path=output_mask_path,
            )
            if not analysis:
                return VisionSegmentationAnalysis(
                    status="unavailable",
                    provider="apple_vision_person_segmentation",
                    view=view,
                    image_path=str(image_path),
                    mask_path=str(output_mask_path),
                    notes=["Apple Vision person segmentation was unavailable for this image."],
                )
            if analysis.status == "ok" and mask_type:
                pose = pose_analysis or self.apple_vision.analyze_body_pose(image_path=image_path, view=view)
                return self._build_agnostic_mask(
                    image_path=image_path,
                    segmentation=analysis,
                    output_mask_path=output_mask_path,
                    mask_type=mask_type,
                    pose_analysis=pose,
                )
            return analysis
        if backend in {"schp", "detectron2", "catvton_automasker"}:
            return self._run_external_backend(
                image_path=image_path,
                view=view,
                output_mask_path=output_mask_path,
                provider=backend,
                mask_type=mask_type or "overall",
            )
        return VisionSegmentationAnalysis(
            status="unavailable",
            provider=backend,
            view=view,
            image_path=str(image_path),
            mask_path=str(output_mask_path),
            notes=[f"Unsupported human segmentation backend: {backend}"],
        )

    def _build_agnostic_mask(
        self,
        *,
        image_path: str | Path,
        segmentation: VisionSegmentationAnalysis,
        output_mask_path: str | Path,
        mask_type: str,
        pose_analysis: VisionBodyAnalysis | None,
    ) -> VisionSegmentationAnalysis:
        mask_file = Path(segmentation.mask_path or output_mask_path)
        if not mask_file.exists():
            return VisionSegmentationAnalysis(
                status="unavailable",
                provider="apple_vision_person_segmentation",
                view=segmentation.view,
                image_path=str(image_path),
                mask_path=str(output_mask_path),
                notes=["Base person mask for agnostic generation was missing."],
            )
        person_mask = Image.open(mask_file).convert("L")
        person_mask_arr = np.array(person_mask)
        binary_person = person_mask_arr > 127
        if not binary_person.any():
            return VisionSegmentationAnalysis(
                status="unavailable",
                provider="apple_vision_agnostic_mask",
                view=segmentation.view,
                image_path=str(image_path),
                mask_path=str(output_mask_path),
                notes=["Base person mask was empty."],
            )

        height, width = binary_person.shape
        ys, xs = np.where(binary_person)
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())

        def point(name: str) -> tuple[float, float] | None:
            if not pose_analysis or pose_analysis.status != "ok" or name not in pose_analysis.points:
                return None
            p = pose_analysis.points[name]
            return (p.x * width, (1.0 - p.y) * height)

        shoulders = [p for p in [point("leftShoulder"), point("rightShoulder")] if p]
        hips = [p for p in [point("leftHip"), point("rightHip")] if p]
        knees = [p for p in [point("leftKnee"), point("rightKnee")] if p]
        wrists = [p for p in [point("leftWrist"), point("rightWrist")] if p]
        nose = point("nose")

        shoulder_y = min([p[1] for p in shoulders], default=y0 + 0.18 * (y1 - y0))
        hip_y = max([p[1] for p in hips], default=y0 + 0.58 * (y1 - y0))
        knee_y = max([p[1] for p in knees], default=y0 + 0.84 * (y1 - y0))
        upper_left = min([p[0] for p in shoulders + wrists], default=x0 + 0.18 * (x1 - x0))
        upper_right = max([p[0] for p in shoulders + wrists], default=x1 - 0.18 * (x1 - x0))
        hip_left = min([p[0] for p in hips], default=x0 + 0.25 * (x1 - x0))
        hip_right = max([p[0] for p in hips], default=x1 - 0.25 * (x1 - x0))

        canvas = Image.new("L", (width, height), 0)
        draw = ImageDraw.Draw(canvas)
        part = mask_type.lower()

        if part in {"upper", "outer", "top", "outerwear"}:
            draw.polygon(
                [
                    (upper_left - 0.06 * width, shoulder_y - 0.05 * height),
                    (upper_right + 0.06 * width, shoulder_y - 0.05 * height),
                    (hip_right + 0.03 * width, hip_y + 0.04 * height),
                    (hip_left - 0.03 * width, hip_y + 0.04 * height),
                ],
                fill=255,
            )
        elif part in {"lower", "bottom", "footwear"}:
            lower_left = min(hip_left, x0 + 0.18 * (x1 - x0))
            lower_right = max(hip_right, x1 - 0.18 * (x1 - x0))
            draw.polygon(
                [
                    (lower_left - 0.03 * width, hip_y - 0.01 * height),
                    (lower_right + 0.03 * width, hip_y - 0.01 * height),
                    (x1 - 0.08 * (x1 - x0), knee_y + 0.14 * height),
                    (x0 + 0.08 * (x1 - x0), knee_y + 0.14 * height),
                ],
                fill=255,
            )
        else:
            draw.rectangle(
                [
                    x0 + 0.04 * (x1 - x0),
                    shoulder_y - 0.04 * height,
                    x1 - 0.04 * (x1 - x0),
                    knee_y + 0.08 * height,
                ],
                fill=255,
            )

        # Remove face and hands from the replacement area.
        if nose:
            head_radius = int(max(width, height) * 0.06)
            draw.ellipse(
                [nose[0] - head_radius, nose[1] - 1.2 * head_radius, nose[0] + head_radius, nose[1] + head_radius],
                fill=0,
            )
        for wrist in wrists:
            hand_radius = int(max(width, height) * 0.035)
            draw.ellipse(
                [wrist[0] - hand_radius, wrist[1] - hand_radius, wrist[0] + hand_radius, wrist[1] + hand_radius],
                fill=0,
            )

        agnostic = np.array(canvas) > 0
        agnostic &= binary_person
        # Smooth and slightly expand edges to match CatVTON's expected mask softness.
        agnostic_img = Image.fromarray((agnostic.astype(np.uint8) * 255)).filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(2))
        agnostic_arr = (np.array(agnostic_img) > 18).astype(np.uint8) * 255
        Image.fromarray(agnostic_arr).save(output_mask_path)

        out_binary = agnostic_arr > 127
        ys, xs = np.where(out_binary)
        bbox_x = bbox_y = bbox_width = bbox_height = None
        coverage = 0.0
        if len(xs) and len(ys):
            bbox_x = round(float(xs.min() / width), 4)
            bbox_y = round(float(ys.min() / height), 4)
            bbox_width = round(float((xs.max() - xs.min() + 1) / width), 4)
            bbox_height = round(float((ys.max() - ys.min() + 1) / height), 4)
            coverage = round(float(out_binary.mean()), 4)

        notes = [
            f"Generated category-aware agnostic mask from Apple Vision person segmentation for mask_type={mask_type}.",
            "This is a geometry-guided fallback, not SCHP/DensePose parsing.",
        ]
        if not pose_analysis or pose_analysis.status != "ok":
            notes.append("Body pose landmarks were unavailable, so torso/leg regions used bbox heuristics.")

        return VisionSegmentationAnalysis(
            status="ok",
            provider="apple_vision_agnostic_mask",
            view=segmentation.view,
            image_path=str(image_path),
            image_width=segmentation.image_width,
            image_height=segmentation.image_height,
            mask_path=str(output_mask_path),
            bbox_x=bbox_x,
            bbox_y=bbox_y,
            bbox_width=bbox_width,
            bbox_height=bbox_height,
            coverage_score=coverage,
            notes=notes,
        )

    def _run_external_backend(
        self,
        *,
        image_path: str | Path,
        view: str,
        output_mask_path: str | Path,
        provider: str,
        mask_type: str,
    ) -> VisionSegmentationAnalysis:
        if not self.config.runner_command:
            return VisionSegmentationAnalysis(
                status="unavailable",
                provider=provider,
                view=view,
                image_path=str(image_path),
                mask_path=str(output_mask_path),
                notes=[
                    f"{provider} backend requested, but MIRA_STYLIST_HUMAN_SEGMENTATION_RUNNER is not configured.",
                    "Set a runner command that emits a VisionSegmentationAnalysis-compatible JSON payload.",
                ],
            )
        command = self.config.runner_command.format(
            image_path=str(image_path),
            view=view,
            output_mask_path=str(output_mask_path),
            provider=provider,
            mask_type=mask_type,
        )
        try:
            completed = subprocess.run(
                shlex.split(command),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.config.timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return VisionSegmentationAnalysis(
                status="runner_error",
                provider=provider,
                view=view,
                image_path=str(image_path),
                mask_path=str(output_mask_path),
                notes=[f"Failed to execute human segmentation runner: {exc}"],
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            payload = {
                "status": "invalid_runner_output",
                "provider": provider,
                "view": view,
                "image_path": str(image_path),
                "mask_path": str(output_mask_path),
                "notes": [completed.stderr.strip() or "Human segmentation runner did not emit valid JSON."],
            }
        if hasattr(VisionSegmentationAnalysis, "model_validate"):
            return VisionSegmentationAnalysis.model_validate(payload)
        return VisionSegmentationAnalysis.parse_obj(payload)
