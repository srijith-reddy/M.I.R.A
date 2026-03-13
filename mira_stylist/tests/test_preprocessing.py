from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from mira_stylist.models.vision import VisionBodyAnalysis, VisionKeypoint, VisionSegmentationAnalysis
from mira_stylist.vision.garment_segmentation import GarmentSegmentationEngine
from mira_stylist.vision.human_segmentation import HumanSegmentationEngine


class PreprocessingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mira_stylist_pre_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_refined_alpha_segments_center_garment(self) -> None:
        image_path = self.temp_dir / "garment.png"
        image = Image.new("RGB", (120, 160), (245, 245, 245))
        draw = ImageDraw.Draw(image)
        draw.rectangle((30, 15, 90, 145), fill=(130, 90, 50))
        image.save(image_path)

        engine = GarmentSegmentationEngine()
        result = engine.segment(image_path, output_dir=self.temp_dir / "garment_seg", category_hint="top", text_prompt="shirt")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.provider, "refined_alpha")
        self.assertLess(result.bbox_width or 1.0, 0.8)
        self.assertGreater(result.bbox_height or 0.0, 0.45)
        self.assertTrue(Path(result.mask_path).exists())
        self.assertTrue(Path(result.alpha_png_path).exists())

    def test_agnostic_upper_mask_covers_torso_not_whole_body(self) -> None:
        base_mask_path = self.temp_dir / "person_mask.png"
        output_mask_path = self.temp_dir / "agnostic_mask.png"

        mask = Image.new("L", (100, 200), 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle((25, 10, 75, 195), radius=10, fill=255)
        mask.save(base_mask_path)

        segmentation = VisionSegmentationAnalysis(
            status="ok",
            provider="apple_vision_person_segmentation",
            view="front",
            image_path=str(base_mask_path),
            image_width=100,
            image_height=200,
            mask_path=str(base_mask_path),
        )
        pose = VisionBodyAnalysis(
            status="ok",
            provider="apple_vision_body_pose",
            view="front",
            image_path=str(base_mask_path),
            image_width=100,
            image_height=200,
            points={
                "leftShoulder": VisionKeypoint(x=0.65, y=0.75, confidence=0.8),
                "rightShoulder": VisionKeypoint(x=0.35, y=0.75, confidence=0.8),
                "leftHip": VisionKeypoint(x=0.58, y=0.45, confidence=0.8),
                "rightHip": VisionKeypoint(x=0.42, y=0.45, confidence=0.8),
                "leftWrist": VisionKeypoint(x=0.72, y=0.58, confidence=0.8),
                "rightWrist": VisionKeypoint(x=0.28, y=0.58, confidence=0.8),
                "nose": VisionKeypoint(x=0.5, y=0.9, confidence=0.8),
            },
        )

        engine = HumanSegmentationEngine()
        result = engine._build_agnostic_mask(
            image_path=base_mask_path,
            segmentation=segmentation,
            output_mask_path=output_mask_path,
            mask_type="upper",
            pose_analysis=pose,
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.provider, "apple_vision_agnostic_mask")
        mask_array = np.array(Image.open(output_mask_path).convert("L")) > 127
        self.assertTrue(mask_array.any())
        self.assertLess(mask_array.mean(), 0.45)
        self.assertFalse(mask_array[-10:, :].any())

    def test_segment_creates_mask_parent_directory(self) -> None:
        image_path = self.temp_dir / "user.png"
        image = Image.new("RGB", (32, 32), (255, 255, 255))
        image.save(image_path)

        class StubAppleVision:
            def analyze_person_segmentation(self, image_path, view, output_mask_path):
                Image.new("L", (32, 32), 255).save(output_mask_path)
                return VisionSegmentationAnalysis(
                    status="ok",
                    provider="apple_vision_person_segmentation",
                    view=view,
                    image_path=str(image_path),
                    image_width=32,
                    image_height=32,
                    mask_path=str(output_mask_path),
                    coverage_score=1.0,
                )

            def analyze_body_pose(self, image_path, view):
                return None

        nested_mask_path = self.temp_dir / "nested" / "masks" / "person.png"
        engine = HumanSegmentationEngine(apple_vision=StubAppleVision())
        result = engine.segment(image_path, output_mask_path=nested_mask_path)

        self.assertEqual(result.status, "ok")
        self.assertTrue(nested_mask_path.exists())


if __name__ == "__main__":
    unittest.main()
