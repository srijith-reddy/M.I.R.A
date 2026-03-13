from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")


def _result(status: str, backend: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": status, "backend": backend, "notes": extra.pop("notes", [])}
    payload.update(extra)
    return payload


def _resolve_device(mode: str, torch_module) -> str:
    if mode == "local_cpu":
        return "cpu"
    if torch_module.cuda.is_available():
        return "cuda"
    if hasattr(torch_module.backends, "mps") and torch_module.backends.mps.is_available():
        return "mps"
    return "cpu"


def _resolve_dtype(torch_module, device: str, precision: str):
    precision = precision.lower()
    mapping = {
        "no": torch_module.float32,
        "fp16": torch_module.float16,
        "bf16": torch_module.bfloat16,
    }
    dtype = mapping.get(precision, torch_module.float32)
    if device in {"cpu", "mps"} and dtype != torch_module.float32:
        return torch_module.float32
    return dtype


def main() -> int:
    if len(sys.argv) != 3:
        print(json.dumps(_result("invalid_input", "catvton_local", notes=["Expected request_json and output_dir arguments."])))
        return 1

    request_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = json.loads(request_path.read_text(encoding="utf-8"))

    repo_path = Path(os.getenv("MIRA_STYLIST_CATVTON_REPO_PATH", "")).expanduser()
    if not repo_path.exists():
        print(json.dumps(_result("model_unavailable", "catvton_local", notes=[f"CatVTON repo path does not exist: {repo_path}"])))
        return 0
    if not (repo_path / "model" / "pipeline.py").exists():
        print(json.dumps(_result("model_unavailable", "catvton_local", notes=[f"CatVTON checkout at {repo_path} does not contain model/pipeline.py"])))
        return 0

    sys.path.insert(0, str(repo_path))

    try:
        import torch
        from PIL import Image, ImageOps
        from model.pipeline import CatVTONPipeline
        from utils import init_weight_dtype, resize_and_crop, resize_and_padding
    except Exception as exc:
        print(json.dumps(_result("runtime_error", "catvton_local", notes=[f"Failed to import CatVTON runtime: {exc}"])))
        return 0

    mode = os.getenv("MIRA_STYLIST_CATVTON_MODE", "local_mps")
    device = _resolve_device(mode, torch)
    mixed_precision = os.getenv("MIRA_STYLIST_CATVTON_MIXED_PRECISION", "no")
    weight_dtype = _resolve_dtype(torch, device, mixed_precision)
    base_model_path = os.getenv("MIRA_STYLIST_CATVTON_BASE_MODEL_PATH", "booksforcharlie/stable-diffusion-inpainting")
    resume_path = os.getenv("MIRA_STYLIST_CATVTON_RESUME_PATH", "zhengchong/CatVTON")
    attn_version = os.getenv("MIRA_STYLIST_CATVTON_ATTN_VERSION", "mix")
    width = int(os.getenv("MIRA_STYLIST_CATVTON_WIDTH", "768"))
    height = int(os.getenv("MIRA_STYLIST_CATVTON_HEIGHT", "1024"))
    num_inference_steps = int(os.getenv("MIRA_STYLIST_CATVTON_STEPS", "30"))
    guidance_scale = float(os.getenv("MIRA_STYLIST_CATVTON_GUIDANCE_SCALE", "2.5"))
    seed = int(os.getenv("MIRA_STYLIST_CATVTON_SEED", "42"))
    repaint = os.getenv("MIRA_STYLIST_CATVTON_REPAINT", "true").strip().lower() not in {"0", "false", "no"}
    skip_safety_check = os.getenv("MIRA_STYLIST_CATVTON_SKIP_SAFETY_CHECK", "false").strip().lower() in {"1", "true", "yes"}

    try:
        person_image = ImageOps.exif_transpose(Image.open(payload["avatar_image_path"])).convert("RGB")
        garment_image = ImageOps.exif_transpose(Image.open(payload["garment_image_path"])).convert("RGB")
        mask_path = payload.get("person_segmentation_path")
        if not mask_path or not Path(mask_path).exists():
            print(json.dumps(_result("input_error", "catvton_local", notes=["CatVTON requires a person segmentation mask path."])))
            return 0
        person_mask = ImageOps.exif_transpose(Image.open(mask_path)).convert("L")
    except Exception as exc:
        print(json.dumps(_result("input_error", "catvton_local", notes=[f"Failed to open CatVTON inputs: {exc}"])))
        return 0

    try:
        pipeline = CatVTONPipeline(
            base_ckpt=base_model_path,
            attn_ckpt=resume_path,
            attn_ckpt_version=attn_version,
            weight_dtype=weight_dtype,
            device=device,
            skip_safety_check=skip_safety_check,
            use_tf32=device == "cuda",
        )
        generator = torch.Generator(device=device).manual_seed(seed) if seed >= 0 else None
        person_resized = resize_and_crop(person_image, (width, height))
        garment_resized = resize_and_padding(garment_image, (width, height))
        mask_resized = resize_and_crop(person_mask, (width, height))
        result_image = pipeline(
            image=person_resized,
            condition_image=garment_resized,
            mask=mask_resized,
            num_inference_steps=num_inference_steps,
            guidance_scale=guidance_scale,
            height=height,
            width=width,
            generator=generator,
        )[0]
        if repaint:
            mask_arr = np.array(mask_resized).astype("float32") / 255.0
            person_arr = np.array(person_resized).astype("float32")
            result_arr = np.array(result_image).astype("float32")
            blended = result_arr * mask_arr[..., None] + person_arr * (1.0 - mask_arr[..., None])
            result_image = Image.fromarray(blended.clip(0, 255).astype("uint8"))
    except Exception as exc:
        print(
            json.dumps(
                _result(
                    "runtime_error",
                    "catvton_local",
                    notes=[
                        f"CatVTON inference failed: {exc}",
                        "Verify the CatVTON checkout, Hugging Face model access, and local torch backend support.",
                    ],
                )
            )
        )
        return 0

    result_path = output_dir / "catvton_result.png"
    mask_copy_path = output_dir / "person_mask.png"
    garment_copy_path = output_dir / "garment_input.png"
    result_image.save(result_path)
    person_mask.save(mask_copy_path)
    garment_resized.save(garment_copy_path)
    print(
        json.dumps(
            _result(
                "ok",
                "catvton_local",
                generated_preview_path=str(result_path),
                generated_auxiliary_paths={
                    "person_mask": str(mask_copy_path),
                    "garment_input": str(garment_copy_path),
                },
                notes=[
                    "Generated by local CatVTON runner.",
                    f"device={device}",
                    f"precision={mixed_precision}",
                    f"skip_safety_check={skip_safety_check}",
                ],
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
