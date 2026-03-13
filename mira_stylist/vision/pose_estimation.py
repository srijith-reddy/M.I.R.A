from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from mira_stylist.models.vision import VisionBodyAnalysis

if TYPE_CHECKING:
    from mira_stylist.services.apple_vision_service import AppleVisionService


@dataclass(frozen=True)
class PoseEstimationConfig:
    backend: str = field(default_factory=lambda: os.getenv("MIRA_STYLIST_POSE_BACKEND", "apple_vision").strip() or "apple_vision")
    runner_command: str = field(default_factory=lambda: os.getenv("MIRA_STYLIST_POSE_RUNNER", "").strip())
    timeout_seconds: int = field(default_factory=lambda: int(os.getenv("MIRA_STYLIST_POSE_TIMEOUT_SECONDS", "30")))


class PoseEstimationEngine:
    """Select between Apple Vision and heavier external pose backends."""

    def __init__(
        self,
        config: PoseEstimationConfig | None = None,
        apple_vision: "AppleVisionService | None" = None,
    ) -> None:
        self.config = config or PoseEstimationConfig()
        if apple_vision is None:
            from mira_stylist.services.apple_vision_service import AppleVisionService

            apple_vision = AppleVisionService(timeout_seconds=min(self.config.timeout_seconds, 12))
        self.apple_vision = apple_vision

    def estimate(self, image_path: str | Path, *, view: str = "front") -> VisionBodyAnalysis:
        backend = self.config.backend.lower()
        if backend == "apple_vision":
            analysis = self.apple_vision.analyze_body_pose(image_path, view=view)
            return analysis or VisionBodyAnalysis(
                status="unavailable",
                provider="apple_vision_body_pose",
                view=view,
                image_path=str(image_path),
                notes=["Apple Vision body pose analysis was unavailable for this image."],
            )
        if backend in {"dwpose", "openpose"}:
            return self._run_external_backend(image_path=image_path, view=view, provider=backend)
        return VisionBodyAnalysis(
            status="unavailable",
            provider=backend,
            view=view,
            image_path=str(image_path),
            notes=[f"Unsupported pose backend: {backend}"],
        )

    def _run_external_backend(self, *, image_path: str | Path, view: str, provider: str) -> VisionBodyAnalysis:
        if not self.config.runner_command:
            return VisionBodyAnalysis(
                status="unavailable",
                provider=provider,
                view=view,
                image_path=str(image_path),
                notes=[
                    f"{provider} backend requested, but MIRA_STYLIST_POSE_RUNNER is not configured.",
                    "Set a runner command that emits a VisionBodyAnalysis-compatible JSON payload.",
                ],
            )
        command = self.config.runner_command.format(image_path=str(image_path), view=view, provider=provider)
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
            return VisionBodyAnalysis(
                status="runner_error",
                provider=provider,
                view=view,
                image_path=str(image_path),
                notes=[f"Failed to execute pose runner: {exc}"],
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            payload = {
                "status": "invalid_runner_output",
                "provider": provider,
                "view": view,
                "image_path": str(image_path),
                "notes": [completed.stderr.strip() or "Pose runner did not emit valid JSON."],
            }
        if hasattr(VisionBodyAnalysis, "model_validate"):
            return VisionBodyAnalysis.model_validate(payload)
        return VisionBodyAnalysis.parse_obj(payload)
