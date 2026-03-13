from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")


def _load_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_json_if_exists(path_value: str | None) -> dict[str, Any]:
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _resolve_device(torch_module) -> str:
    requested = os.getenv("MIRA_STYLIST_VTON_DEVICE", "auto").strip().lower() or "auto"
    if requested != "auto":
        return requested
    if torch_module.cuda.is_available():
        return "cuda"
    if hasattr(torch_module.backends, "mps") and torch_module.backends.mps.is_available():
        return "mps"
    return "cpu"


def _resolve_dtype(torch_module, device: str):
    requested = os.getenv("MIRA_STYLIST_VTON_DTYPE", "float32").strip().lower() or "float32"
    mapping = {
        "float32": torch_module.float32,
        "fp32": torch_module.float32,
        "float16": torch_module.float16,
        "fp16": torch_module.float16,
        "bfloat16": torch_module.bfloat16,
        "bf16": torch_module.bfloat16,
    }
    dtype = mapping.get(requested, torch_module.float32)
    if device == "cpu" and dtype != torch_module.float32:
        return torch_module.float32
    return dtype


def _normalized_point(point: dict[str, Any] | None, width: int, height: int) -> tuple[float, float] | None:
    if not point:
        return None
    x = float(point.get("x", 0.5)) * width
    y = (1.0 - float(point.get("y", 0.5))) * height
    return (x, y)


def _person_bbox(segmentation_meta: dict[str, Any], width: int, height: int) -> tuple[int, int, int, int]:
    x = int(float(segmentation_meta.get("bbox_x", 0.2)) * width)
    y = int(float(segmentation_meta.get("bbox_y", 0.08)) * height)
    w = int(float(segmentation_meta.get("bbox_width", 0.6)) * width)
    h = int(float(segmentation_meta.get("bbox_height", 0.84)) * height)
    return (max(0, x), max(0, y), min(width, x + w), min(height, y + h))


def _target_zone(
    payload: dict[str, Any],
    pose_meta: dict[str, Any],
    segmentation_meta: dict[str, Any],
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    category = (payload.get("garment_category") or "top").lower()
    bbox = _person_bbox(segmentation_meta, width, height)
    left, top, right, bottom = bbox
    box_width = max(1, right - left)
    box_height = max(1, bottom - top)

    points = pose_meta.get("points") or {}
    ls = _normalized_point(points.get("leftShoulder"), width, height)
    rs = _normalized_point(points.get("rightShoulder"), width, height)
    lh = _normalized_point(points.get("leftHip"), width, height)
    rh = _normalized_point(points.get("rightHip"), width, height)
    lk = _normalized_point(points.get("leftKnee"), width, height)
    rk = _normalized_point(points.get("rightKnee"), width, height)
    la = _normalized_point(points.get("leftAnkle"), width, height)
    ra = _normalized_point(points.get("rightAnkle"), width, height)

    if category in {"top", "outerwear", "dress"} and ls and rs and lh and rh:
        zone_left = int(max(0, min(ls[0], lh[0]) - box_width * 0.12))
        zone_right = int(min(width, max(rs[0], rh[0]) + box_width * 0.12))
        zone_top = int(max(0, min(ls[1], rs[1]) - box_height * 0.08))
        zone_bottom = int(min(height, max(lh[1], rh[1]) + (box_height * (0.18 if category != "dress" else 0.5))))
        return (zone_left, zone_top, max(zone_left + 1, zone_right), max(zone_top + 1, zone_bottom))

    if category == "bottom" and lh and rh:
        knee_y = max((lk[1] if lk else lh[1] + box_height * 0.28), (rk[1] if rk else rh[1] + box_height * 0.28))
        ankle_y = max((la[1] if la else bottom), (ra[1] if ra else bottom))
        zone_left = int(max(0, min(lh[0], rh[0]) - box_width * 0.08))
        zone_right = int(min(width, max(lh[0], rh[0]) + box_width * 0.08))
        zone_top = int(max(0, min(lh[1], rh[1]) - box_height * 0.02))
        zone_bottom = int(min(height, max(knee_y + box_height * 0.12, ankle_y)))
        return (zone_left, zone_top, max(zone_left + 1, zone_right), max(zone_top + 1, zone_bottom))

    return (
        int(left + box_width * 0.18),
        int(top + box_height * 0.15),
        int(left + box_width * 0.82),
        int(top + box_height * 0.62),
    )


def _make_mask(image_size: tuple[int, int], zone: tuple[int, int, int, int], person_mask):
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    width, height = image_size
    base = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(base)
    draw.rounded_rectangle(zone, radius=max(12, int((zone[2] - zone[0]) * 0.08)), fill=255)
    if person_mask is not None:
        person = person_mask.convert("L").resize((width, height))
        base = ImageChops.multiply(base, person)
    return base.filter(ImageFilter.GaussianBlur(radius=12))


def _prepare_conditioned_init(avatar_image, garment_image, zone, person_mask):
    from PIL import Image, ImageChops, ImageFilter

    base = avatar_image.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    zone_left, zone_top, zone_right, zone_bottom = zone
    zone_width = max(1, zone_right - zone_left)
    zone_height = max(1, zone_bottom - zone_top)

    garment = garment_image.convert("RGBA")
    scale = min(zone_width / max(1, garment.width), zone_height / max(1, garment.height))
    scaled_size = (max(1, int(garment.width * scale)), max(1, int(garment.height * scale)))
    garment = garment.resize(scaled_size)
    paste_x = zone_left + max(0, (zone_width - garment.width) // 2)
    paste_y = zone_top + max(0, (zone_height - garment.height) // 2)
    overlay.paste(garment, (paste_x, paste_y), garment)

    if person_mask is not None:
        person_mask = person_mask.convert("L").resize(base.size).filter(ImageFilter.GaussianBlur(radius=4))
        alpha = overlay.getchannel("A")
        overlay.putalpha(ImageChops.multiply(alpha, person_mask))

    return Image.alpha_composite(base, overlay).convert("RGB")


def _result(status: str, backend: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": status, "backend": backend, "notes": extra.pop("notes", [])}
    payload.update(extra)
    return payload


def main() -> int:
    if len(sys.argv) != 3:
        print(json.dumps(_result("invalid_input", "diffusers_local", notes=["Expected request_json and output_dir arguments."])))
        return 1

    request_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = _load_payload(request_path)

    model_path = os.getenv("MIRA_STYLIST_VTON_MODEL_PATH", "").strip()
    if not model_path:
        print(json.dumps(_result("model_unavailable", "diffusers_local", notes=["MIRA_STYLIST_VTON_MODEL_PATH is not set."])))
        return 0
    if not Path(model_path).exists():
        print(json.dumps(_result("model_unavailable", "diffusers_local", notes=[f"Configured model path does not exist: {model_path}"])))
        return 0

    try:
        import torch
        from diffusers import AutoPipelineForInpainting
        from PIL import Image
    except Exception as exc:
        print(json.dumps(_result("runtime_error", "diffusers_local", notes=[f"Failed to import ML runtime: {exc}"])))
        return 0

    try:
        avatar_image = Image.open(payload["avatar_image_path"]).convert("RGB")
        garment_image = Image.open(payload["garment_image_path"]).convert("RGBA")
    except Exception as exc:
        print(json.dumps(_result("input_error", "diffusers_local", notes=[f"Failed to open source images: {exc}"])))
        return 0

    pose_meta = _load_json_if_exists(payload.get("pose_metadata_path"))
    segmentation_meta = _load_json_if_exists(payload.get("person_segmentation_metadata_path"))
    person_mask = None
    mask_path_value = payload.get("person_segmentation_path")
    if mask_path_value and Path(mask_path_value).exists():
        try:
            person_mask = Image.open(mask_path_value).convert("L")
        except Exception:
            person_mask = None

    zone = _target_zone(payload, pose_meta, segmentation_meta, avatar_image.width, avatar_image.height)
    mask_image = _make_mask(avatar_image.size, zone, person_mask)
    conditioned_init = _prepare_conditioned_init(avatar_image, garment_image, zone, person_mask)

    device = _resolve_device(torch)
    torch_dtype = _resolve_dtype(torch, device)
    num_steps = max(8, int(os.getenv("MIRA_STYLIST_VTON_STEPS", "24")))
    guidance_scale = float(os.getenv("MIRA_STYLIST_VTON_GUIDANCE_SCALE", "6.5"))
    strength = float(os.getenv("MIRA_STYLIST_VTON_STRENGTH", "0.88"))
    color = (payload.get("garment_color") or "").strip()
    category = (payload.get("garment_category") or "garment").replace("_", " ")
    garment_phrase = f"{color} {category}".strip()
    prompt = (
        f"photorealistic fashion try-on, person wearing a {garment_phrase}, "
        "preserve face hair hands background, realistic clothing folds, studio quality, natural fit"
    )
    negative_prompt = (
        "floating garment, duplicate clothing, distorted body, extra limbs, blurry face, malformed hands, "
        "cartoon, mannequin, flat overlay, text, watermark"
    )

    try:
        pipeline = AutoPipelineForInpainting.from_pretrained(model_path, torch_dtype=torch_dtype)
        if hasattr(pipeline, "enable_attention_slicing"):
            pipeline.enable_attention_slicing()
        if device == "cpu" and hasattr(pipeline, "enable_model_cpu_offload"):
            pass
        else:
            pipeline = pipeline.to(device)
        result = pipeline(
            prompt=prompt,
            negative_prompt=negative_prompt,
            image=conditioned_init,
            mask_image=mask_image,
            num_inference_steps=num_steps,
            guidance_scale=guidance_scale,
            strength=strength,
        )
        output = result.images[0]
    except Exception as exc:
        print(
            json.dumps(
                _result(
                    "runtime_error",
                    "diffusers_local",
                    notes=[
                        f"Learned VTON backend failed during inference: {exc}",
                        "This backend expects a local diffusers inpainting checkpoint.",
                    ],
                )
            )
        )
        return 0

    output_path = output_dir / "front_vton.png"
    output.save(output_path)
    result = _result(
        "ok",
        "diffusers_local",
        generated_preview_path=str(output_path),
        generated_auxiliary_paths={"vton_mask": str(output_dir / "mask.png"), "vton_conditioned_init": str(output_dir / "conditioned_init.png")},
        notes=[
            "Generated by the local diffusers-based learned VTON backend.",
            "This is a learned synthesis path, but still depends on the configured model quality and preprocessing.",
            f"category={category}",
            f"device={device}",
        ],
    )
    mask_image.save(output_dir / "mask.png")
    conditioned_init.save(output_dir / "conditioned_init.png")
    (output_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
