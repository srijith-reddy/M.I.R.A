from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps


@dataclass(frozen=True)
class GarmentSegmentationConfig:
    backend: str = field(
        default_factory=lambda: os.getenv("MIRA_STYLIST_GARMENT_SEGMENTATION_BACKEND", "refined_alpha").strip() or "refined_alpha"
    )
    runner_command: str = field(default_factory=lambda: os.getenv("MIRA_STYLIST_GARMENT_SEGMENTATION_RUNNER", "").strip())
    timeout_seconds: int = field(default_factory=lambda: int(os.getenv("MIRA_STYLIST_GARMENT_SEGMENTATION_TIMEOUT_SECONDS", "60")))
    edge_trim_ratio: float = field(default_factory=lambda: float(os.getenv("MIRA_STYLIST_GARMENT_SEGMENTATION_EDGE_TRIM_RATIO", "0.08")))


@dataclass
class GarmentSegmentationResult:
    status: str
    provider: str
    image_path: str
    mask_path: str
    alpha_png_path: str
    bbox_x: float | None = None
    bbox_y: float | None = None
    bbox_width: float | None = None
    bbox_height: float | None = None
    coverage_score: float = 0.0
    notes: list[str] = field(default_factory=list)


class GarmentSegmentationEngine:
    """Segment garment product shots into alpha PNGs for try-on conditioning."""

    def __init__(self, config: GarmentSegmentationConfig | None = None) -> None:
        self.config = config or GarmentSegmentationConfig()

    def segment(
        self,
        image_path: str | Path,
        *,
        output_dir: str | Path,
        category_hint: str | None = None,
        text_prompt: str | None = None,
    ) -> GarmentSegmentationResult:
        backend = self.config.backend.lower()
        image_path = str(image_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        mask_path = output_dir / "garment_mask.png"
        alpha_png_path = output_dir / "garment_alpha.png"
        metadata_path = output_dir / "garment_segmentation.json"
        if backend == "groundingdino_sam":
            result = self._run_external_backend(
                image_path=image_path,
                output_dir=output_dir,
                mask_path=mask_path,
                alpha_png_path=alpha_png_path,
                category_hint=category_hint,
                text_prompt=text_prompt,
                provider=backend,
            )
        elif backend == "simple_alpha":
            result = self._simple_alpha_mask(
                image_path=Path(image_path),
                mask_path=mask_path,
                alpha_png_path=alpha_png_path,
            )
        else:
            result = self._refined_alpha_mask(
                image_path=Path(image_path),
                mask_path=mask_path,
                alpha_png_path=alpha_png_path,
            )
        metadata_path.write_text(json.dumps(result.__dict__, indent=2), encoding="utf-8")
        return result

    def _run_external_backend(
        self,
        *,
        image_path: str,
        output_dir: Path,
        mask_path: Path,
        alpha_png_path: Path,
        category_hint: str | None,
        text_prompt: str | None,
        provider: str,
    ) -> GarmentSegmentationResult:
        if not self.config.runner_command:
            return GarmentSegmentationResult(
                status="unavailable",
                provider=provider,
                image_path=image_path,
                mask_path=str(mask_path),
                alpha_png_path=str(alpha_png_path),
                notes=[
                    "GroundingDINO+SAM backend requested, but MIRA_STYLIST_GARMENT_SEGMENTATION_RUNNER is not configured.",
                    "Falling back to simple alpha segmentation is recommended for local development.",
                ],
            )
        command = self.config.runner_command.format(
            image_path=image_path,
            output_dir=str(output_dir),
            mask_path=str(mask_path),
            alpha_png_path=str(alpha_png_path),
            category_hint=category_hint or "",
            text_prompt=text_prompt or "",
            provider=provider,
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
            return GarmentSegmentationResult(
                status="runner_error",
                provider=provider,
                image_path=image_path,
                mask_path=str(mask_path),
                alpha_png_path=str(alpha_png_path),
                notes=[f"Failed to execute garment segmentation runner: {exc}"],
            )
        try:
            payload = json.loads(completed.stdout)
            return GarmentSegmentationResult(**payload)
        except (json.JSONDecodeError, TypeError):
            return GarmentSegmentationResult(
                status="invalid_runner_output",
                provider=provider,
                image_path=image_path,
                mask_path=str(mask_path),
                alpha_png_path=str(alpha_png_path),
                notes=[completed.stderr.strip() or "Garment segmentation runner did not emit valid JSON."],
            )

    def _simple_alpha_mask(self, *, image_path: Path, mask_path: Path, alpha_png_path: Path) -> GarmentSegmentationResult:
        source_image = ImageOps.exif_transpose(Image.open(image_path))
        had_alpha = "A" in source_image.getbands() or "transparency" in source_image.info
        image = source_image.convert("RGBA")
        rgba = np.array(image)
        rgb = rgba[..., :3].astype(np.int16)
        height, width = rgb.shape[:2]
        border = max(1, int(min(width, height) * self.config.edge_trim_ratio))
        border_pixels = np.concatenate(
            [
                rgb[:border, :, :].reshape(-1, 3),
                rgb[-border:, :, :].reshape(-1, 3),
                rgb[:, :border, :].reshape(-1, 3),
                rgb[:, -border:, :].reshape(-1, 3),
            ],
            axis=0,
        )
        background = border_pixels.mean(axis=0)
        distance = np.linalg.norm(rgb - background, axis=2)
        threshold = max(18.0, float(np.percentile(distance, 70)))
        mask = (distance >= threshold).astype(np.uint8) * 255
        if had_alpha and rgba.shape[2] == 4:
            mask = np.maximum(mask, rgba[..., 3])
        ys, xs = np.where(mask > 0)
        if len(xs) == 0 or len(ys) == 0:
            mask = np.full((height, width), 255, dtype=np.uint8)
            bbox_x = bbox_y = 0.0
            bbox_width = bbox_height = 1.0
            coverage = 1.0
            notes = ["Failed to isolate garment foreground; fallback mask covers the whole image."]
        else:
            x0, x1 = int(xs.min()), int(xs.max())
            y0, y1 = int(ys.min()), int(ys.max())
            bbox_x = round(x0 / width, 4)
            bbox_y = round(y0 / height, 4)
            bbox_width = round((x1 - x0 + 1) / width, 4)
            bbox_height = round((y1 - y0 + 1) / height, 4)
            coverage = round(float(mask.mean() / 255.0), 4)
            notes = [
                "Generated by a lightweight edge-background alpha mask fallback.",
                "Use GroundingDINO+SAM for cleaner garment isolation in production.",
            ]
        Image.fromarray(mask).save(mask_path)
        alpha_rgba = rgba.copy()
        alpha_rgba[..., 3] = mask
        Image.fromarray(alpha_rgba).save(alpha_png_path)
        return GarmentSegmentationResult(
            status="ok",
            provider="simple_alpha",
            image_path=str(image_path),
            mask_path=str(mask_path),
            alpha_png_path=str(alpha_png_path),
            bbox_x=bbox_x,
            bbox_y=bbox_y,
            bbox_width=bbox_width,
            bbox_height=bbox_height,
            coverage_score=coverage,
            notes=notes,
        )

    def _refined_alpha_mask(self, *, image_path: Path, mask_path: Path, alpha_png_path: Path) -> GarmentSegmentationResult:
        source_image = ImageOps.exif_transpose(Image.open(image_path))
        had_alpha = "A" in source_image.getbands() or "transparency" in source_image.info
        image = source_image.convert("RGBA")
        rgba = np.array(image)
        rgb = rgba[..., :3].astype(np.float32)
        height, width = rgb.shape[:2]
        border = max(2, int(min(width, height) * self.config.edge_trim_ratio))
        border_pixels = np.concatenate(
            [
                rgb[:border, :, :].reshape(-1, 3),
                rgb[-border:, :, :].reshape(-1, 3),
                rgb[:, :border, :].reshape(-1, 3),
                rgb[:, -border:, :].reshape(-1, 3),
            ],
            axis=0,
        )
        background = np.median(border_pixels, axis=0)
        distance = np.linalg.norm(rgb - background, axis=2)
        border_distance = np.linalg.norm(border_pixels - background, axis=1)
        yy, xx = np.mgrid[0:height, 0:width]
        cx = (xx / max(1, width - 1) - 0.5) / 0.32
        cy = (yy / max(1, height - 1) - 0.5) / 0.42
        center_prior = np.exp(-(cx**2 + cy**2))

        alpha_channel = rgba[..., 3].astype(np.float32) / 255.0
        score = distance * (0.55 + 0.45 * center_prior)
        score_threshold = max(12.0, float(np.percentile(score, 84)))
        distance_threshold = max(float(np.percentile(border_distance, 98)) + 6.0, float(np.percentile(distance, 78)))
        mask = (score >= score_threshold) & (distance >= distance_threshold)
        if had_alpha and rgba.shape[2] == 4:
            mask |= alpha_channel > 0.1

        largest = self._largest_component(mask)
        if largest.any():
            mask = largest
        elif mask.any():
            mask = mask
        else:
            mask = np.ones((height, width), dtype=bool)

        ys, xs = np.where(mask)
        if len(xs) == 0 or len(ys) == 0:
            mask = np.ones((height, width), dtype=bool)
            bbox_x = bbox_y = 0.0
            bbox_width = bbox_height = 1.0
            coverage = 1.0
            notes = ["Refined garment segmentation failed; fallback mask covers the whole image."]
        else:
            x0, x1 = int(xs.min()), int(xs.max())
            y0, y1 = int(ys.min()), int(ys.max())
            bbox_x = round(x0 / width, 4)
            bbox_y = round(y0 / height, 4)
            bbox_width = round((x1 - x0 + 1) / width, 4)
            bbox_height = round((y1 - y0 + 1) / height, 4)
            coverage = round(float(mask.mean()), 4)
            notes = [
                "Generated by refined local alpha segmentation with border-background modeling and largest-component cleanup.",
                "Use GroundingDINO+SAM for stronger arbitrary-web product isolation in production.",
            ]

        mask_u8 = mask.astype(np.uint8) * 255
        Image.fromarray(mask_u8).save(mask_path)
        alpha_rgba = rgba.copy()
        alpha_rgba[..., 3] = mask_u8
        Image.fromarray(alpha_rgba).save(alpha_png_path)
        return GarmentSegmentationResult(
            status="ok",
            provider="refined_alpha",
            image_path=str(image_path),
            mask_path=str(mask_path),
            alpha_png_path=str(alpha_png_path),
            bbox_x=bbox_x,
            bbox_y=bbox_y,
            bbox_width=bbox_width,
            bbox_height=bbox_height,
            coverage_score=coverage,
            notes=notes,
        )

    @staticmethod
    def _largest_component(mask: np.ndarray) -> np.ndarray:
        """Return the largest 4-connected component from a boolean mask."""

        mask = mask.astype(bool)
        visited = np.zeros_like(mask, dtype=bool)
        best_coords: list[tuple[int, int]] = []
        height, width = mask.shape
        for y in range(height):
            for x in range(width):
                if not mask[y, x] or visited[y, x]:
                    continue
                stack = [(y, x)]
                visited[y, x] = True
                coords: list[tuple[int, int]] = []
                while stack:
                    cy, cx = stack.pop()
                    coords.append((cy, cx))
                    for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                        if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not visited[ny, nx]:
                            visited[ny, nx] = True
                            stack.append((ny, nx))
                if len(coords) > len(best_coords):
                    best_coords = coords
        result = np.zeros_like(mask, dtype=bool)
        for y, x in best_coords:
            result[y, x] = True
        return result
