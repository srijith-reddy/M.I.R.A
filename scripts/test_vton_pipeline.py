from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from mira_stylist.models.vton import VTONInputPayload
from mira_stylist.models.vision import VisionBodyAnalysis
from mira_stylist.vision.garment_segmentation import GarmentSegmentationEngine
from mira_stylist.vision.human_segmentation import HumanSegmentationEngine
from mira_stylist.vision.pose_estimation import PoseEstimationEngine
from mira_stylist.vton.catvton_engine import CatVTONEngine


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the MIRA Stylist CatVTON pipeline end to end.")
    parser.add_argument("--user", required=True, help="Path to the user photo.")
    parser.add_argument("--garment", required=True, help="Path to the garment/product image.")
    parser.add_argument("--output-dir", default="outputs", help="Directory for intermediate and final outputs.")
    parser.add_argument("--pose-backend", default=None, help="Optional override for pose backend.")
    parser.add_argument("--human-seg-backend", default=None, help="Optional override for human segmentation backend.")
    parser.add_argument("--garment-seg-backend", default=None, help="Optional override for garment segmentation backend.")
    parser.add_argument("--camera-angle", default="front", help="Camera angle label for pose/segmentation metadata.")
    parser.add_argument("--category", default="top", help="Garment category hint for segmentation and VTON.")
    parser.add_argument("--color", default=None, help="Optional garment color hint.")
    parser.add_argument("--title", default=None, help="Optional garment title.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    user_path = Path(args.user).expanduser().resolve()
    garment_path = Path(args.garment).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    pipeline_dir = output_dir / "pipeline"
    pipeline_dir.mkdir(parents=True, exist_ok=True)

    if not user_path.exists():
        raise SystemExit(f"user image not found: {user_path}")
    if not garment_path.exists():
        raise SystemExit(f"garment image not found: {garment_path}")

    pose_engine = PoseEstimationEngine()
    human_seg_engine = HumanSegmentationEngine()
    garment_seg_engine = GarmentSegmentationEngine()
    catvton = CatVTONEngine()

    if args.pose_backend:
        pose_engine.config = pose_engine.config.__class__(
            backend=args.pose_backend,
            runner_command=pose_engine.config.runner_command,
            timeout_seconds=pose_engine.config.timeout_seconds,
        )
    if args.human_seg_backend:
        human_seg_engine.config = human_seg_engine.config.__class__(
            backend=args.human_seg_backend,
            runner_command=human_seg_engine.config.runner_command,
            timeout_seconds=human_seg_engine.config.timeout_seconds,
        )
    if args.garment_seg_backend:
        garment_seg_engine.config = garment_seg_engine.config.__class__(
            backend=args.garment_seg_backend,
            runner_command=garment_seg_engine.config.runner_command,
            timeout_seconds=garment_seg_engine.config.timeout_seconds,
            edge_trim_ratio=garment_seg_engine.config.edge_trim_ratio,
        )

    pose = pose_engine.estimate(user_path, view=args.camera_angle)
    pose_path = pipeline_dir / "pose.json"
    pose_dict = pose.model_dump() if hasattr(pose, "model_dump") else pose.dict()
    pose_path.write_text(json.dumps(pose_dict, indent=2, default=str), encoding="utf-8")

    human_seg = human_seg_engine.segment(
        user_path,
        view=args.camera_angle,
        output_mask_path=pipeline_dir / "user_mask.png",
        mask_type={"top": "upper", "outerwear": "upper", "dress": "overall", "bottom": "lower", "footwear": "lower"}.get(
            args.category, "overall"
        ),
        pose_analysis=pose if isinstance(pose, VisionBodyAnalysis) else None,
    )
    human_seg_path = pipeline_dir / "human_segmentation.json"
    human_seg_dict = human_seg.model_dump() if hasattr(human_seg, "model_dump") else human_seg.dict()
    human_seg_path.write_text(json.dumps(human_seg_dict, indent=2, default=str), encoding="utf-8")

    garment_seg = garment_seg_engine.segment(
        garment_path,
        output_dir=pipeline_dir / "garment_segmentation",
        category_hint=args.category,
        text_prompt=args.title or args.category,
    )
    garment_seg_path = pipeline_dir / "garment_segmentation.json"
    garment_seg_path.write_text(json.dumps(garment_seg.__dict__, indent=2, default=str), encoding="utf-8")

    payload = VTONInputPayload(
        request_id="manual_test",
        avatar_id="manual_avatar",
        garment_id="manual_garment",
        pose="neutral",
        camera_angle=args.camera_angle,
        avatar_image_path=str(user_path),
        garment_image_path=str(garment_path),
        person_segmentation_path=human_seg.mask_path,
        person_segmentation_metadata_path=str(human_seg_path),
        pose_metadata_path=str(pose_path),
        garment_mask_path=garment_seg.mask_path,
        garment_category=args.category,
        garment_color=args.color,
        garment_title=args.title,
        output_dir=str(output_dir),
        notes=[
            "Generated by scripts/test_vton_pipeline.py.",
            "This script exercises the modular MIRA Stylist pose -> segmentation -> CatVTON path.",
        ],
    )

    vton_result = catvton.run(payload=payload, output_dir=pipeline_dir / "catvton")
    result_path = output_dir / "tryon_result.png"
    summary_path = output_dir / "pipeline_summary.json"

    summary = {
        "pose": pose_dict,
        "human_segmentation": human_seg_dict,
        "garment_segmentation": garment_seg.__dict__,
        "vton_result": (vton_result.model_dump() if hasattr(vton_result, "model_dump") else vton_result.dict()) if vton_result else None,
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    if not vton_result or vton_result.status != "ok" or not vton_result.generated_preview_path:
        raise SystemExit(
            "VTON pipeline did not produce a final preview. "
            f"See {summary_path} for pose/segmentation results and backend notes."
        )

    shutil.copyfile(vton_result.generated_preview_path, result_path)
    print(f"Saved try-on result to {result_path}")
    print(f"Saved pipeline summary to {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
