from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _result(status: str, backend: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": status, "backend": backend, "notes": extra.pop("notes", [])}
    payload.update(extra)
    return payload


def _load_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _body_part(category: str | None) -> str:
    normalized = (category or "upper_body").lower()
    if normalized in {"top", "outerwear"}:
        return "Upper Body"
    if normalized in {"bottom", "footwear"}:
        return "Lower Body"
    if normalized == "dress":
        return "Dresses"
    return "Upper Body"


def _prompt(payload: dict[str, Any]) -> str:
    parts = [payload.get("garment_color"), payload.get("garment_title"), payload.get("garment_category")]
    text = " ".join(str(part).strip() for part in parts if part).strip()
    return text or "clean fashion garment"


def _validate_repo(repo_path: Path) -> list[str]:
    missing: list[str] = []
    if not repo_path.exists():
        missing.append(f"Repo path does not exist: {repo_path}")
        return missing
    for rel in ["gradio_demo/app.py", "inference.py"]:
        if not (repo_path / rel).exists():
            missing.append(f"Missing expected IDM-VTON file: {repo_path / rel}")
    return missing


def main() -> int:
    if len(sys.argv) != 3:
        print(json.dumps(_result("invalid_input", "idm_vton", notes=["Expected request_json and output_dir arguments."])))
        return 1

    request_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = _load_payload(request_path)

    repo_path_value = os.getenv("MIRA_STYLIST_IDM_VTON_REPO_PATH", "").strip()
    server_url = os.getenv("MIRA_STYLIST_IDM_VTON_SERVER_URL", "").strip()
    repo_path = Path(repo_path_value).expanduser() if repo_path_value else None
    if repo_path:
        missing = _validate_repo(repo_path)
        if missing:
            print(json.dumps(_result("repo_invalid", "idm_vton", notes=missing)))
            return 0

    if not server_url:
        notes = [
            "MIRA Stylist IDM-VTON adapter is configured, but no running IDM-VTON demo/API URL is set.",
            "Set MIRA_STYLIST_IDM_VTON_SERVER_URL to the local Gradio demo URL, typically http://127.0.0.1:7860.",
        ]
        if repo_path:
            notes.append(f"Start the local checkout from {repo_path} with its own environment before using this adapter.")
        print(json.dumps(_result("server_unavailable", "idm_vton", notes=notes)))
        return 0

    try:
        from gradio_client import Client, handle_file
    except Exception as exc:
        print(json.dumps(_result("runtime_error", "idm_vton", notes=[f"Failed to import gradio_client: {exc}"])))
        return 0

    try:
        client = Client(server_url)
    except Exception as exc:
        print(json.dumps(_result("server_unavailable", "idm_vton", notes=[f"Failed to connect to IDM-VTON server at {server_url}: {exc}"])))
        return 0

    avatar_path = payload.get("avatar_image_path")
    garment_path = payload.get("garment_image_path")
    if not avatar_path or not garment_path:
        print(json.dumps(_result("input_error", "idm_vton", notes=["Avatar image path or garment image path is missing."])))
        return 0

    denoise_steps = int(os.getenv("MIRA_STYLIST_IDM_VTON_DENOISE_STEPS", "30"))
    seed = int(os.getenv("MIRA_STYLIST_IDM_VTON_SEED", "42"))
    auto_mask = os.getenv("MIRA_STYLIST_IDM_VTON_AUTO_MASK", "true").strip().lower() not in {"0", "false", "no"}
    auto_crop = os.getenv("MIRA_STYLIST_IDM_VTON_AUTO_CROP", "false").strip().lower() in {"1", "true", "yes"}

    try:
        result = client.predict(
            {"background": handle_file(avatar_path), "layers": [], "composite": None},
            handle_file(garment_path),
            _body_part(payload.get("garment_category")),
            _prompt(payload),
            auto_mask,
            auto_crop,
            denoise_steps,
            seed,
            api_name="/tryon",
        )
    except Exception as exc:
        print(
            json.dumps(
                _result(
                    "runtime_error",
                    "idm_vton",
                    notes=[
                        f"IDM-VTON API call failed: {exc}",
                        "Verify the local IDM-VTON checkout is running its Gradio demo and that its environment has all required checkpoints.",
                    ],
                )
            )
        )
        return 0

    if isinstance(result, (list, tuple)) and result:
        primary = result[0]
        secondary = result[1] if len(result) > 1 else None
    else:
        primary = result
        secondary = None

    def _normalize_result_path(value: Any, default_name: str) -> str | None:
        if not value:
            return None
        if isinstance(value, dict):
            value = value.get("path") or value.get("name") or value.get("url")
        candidate = Path(str(value))
        if candidate.exists():
            return str(candidate)
        return str(value)

    generated_preview_path = _normalize_result_path(primary, "front_idm_vton.png")
    generated_mask_path = _normalize_result_path(secondary, "idm_vton_mask.png")
    if not generated_preview_path:
        print(json.dumps(_result("invalid_runner_output", "idm_vton", notes=["IDM-VTON did not return a usable preview artifact."])))
        return 0

    aux: dict[str, str] = {}
    if generated_mask_path:
        aux["idm_vton_mask"] = generated_mask_path

    print(
        json.dumps(
            _result(
                "ok",
                "idm_vton",
                generated_preview_path=generated_preview_path,
                generated_auxiliary_paths=aux,
                notes=[
                    "Generated by IDM-VTON through the local Gradio API adapter.",
                    f"server_url={server_url}",
                    f"body_part={_body_part(payload.get('garment_category'))}",
                ],
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
