from __future__ import annotations

import json
import mimetypes
import os
import shlex
import subprocess
import sys
from pathlib import Path

from mira_stylist.config import StylistSettings, get_settings
from mira_stylist.models import VTONInputPayload, VTONRunResult
from mira_stylist.utils.paths import TryOnStoragePaths
from mira_stylist.vton.catvton_engine import CatVTONEngine


class VTONService:
    """Adapter layer for external learned virtual try-on runners."""
    BUILTIN_DIFFUSERS_RUNNER = Path(__file__).resolve().parent.parent / "tools" / "vton_diffusers_runner.py"
    BUILTIN_IDM_RUNNER = Path(__file__).resolve().parent.parent / "tools" / "idm_vton_runner.py"

    def __init__(self, settings: StylistSettings | None = None):
        self.settings = settings or get_settings()
        self.catvton = CatVTONEngine()

    def generate_preview(
        self,
        *,
        job_id: str,
        storage_paths: TryOnStoragePaths,
        payload: VTONInputPayload | None = None,
    ) -> VTONRunResult | None:
        if payload is None:
            return None
        output_dir = storage_paths.previews_dir / "vton"
        output_dir.mkdir(parents=True, exist_ok=True)

        catvton_result = None
        if not self.settings.vton_runner_command:
            catvton_result = self.catvton.run(payload=payload, output_dir=output_dir / "catvton")
        if catvton_result and catvton_result.status == "ok":
            self._write_result(storage_paths, catvton_result)
            return catvton_result
        if catvton_result and catvton_result.status not in {"unavailable", "model_unavailable"}:
            self._write_result(storage_paths, catvton_result)
            return catvton_result

        request_path = storage_paths.metadata_dir / "vton_request.json"
        request_path.write_text(json.dumps(self._dump_model(payload), indent=2, default=str), encoding="utf-8")

        command = self._render_command(request_json=request_path, output_dir=output_dir)
        if not command:
            if catvton_result:
                self._write_result(storage_paths, catvton_result)
                return catvton_result
            return None
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.settings.vton_timeout_seconds,
                env=self._runner_env(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            result = VTONRunResult(
                status="runner_error",
                backend="external_runner",
                notes=[f"Failed to execute VTON runner: {exc}"],
            )
            self._write_result(storage_paths, result)
            return result

        result = self._parse_runner_result(completed.stdout, output_dir)
        if result is None:
            result = VTONRunResult(
                status="invalid_runner_output",
                backend="external_runner",
                notes=[
                    "VTON runner did not return a parseable result payload.",
                    f"stderr={completed.stderr.strip()}" if completed.stderr.strip() else "stderr=<empty>",
                ],
            )
        elif completed.returncode != 0:
            result.notes.append(f"Runner exited with code {completed.returncode}.")
        if catvton_result and catvton_result.notes:
            result.notes.extend([f"CatVTON preflight: {note}" for note in catvton_result.notes])
        self._write_result(storage_paths, result)
        return result

    def _render_command(self, *, request_json: Path, output_dir: Path) -> list[str]:
        if self.settings.vton_runner_command:
            template = self.settings.vton_runner_command.format(
                request_json=str(request_json),
                output_dir=str(output_dir),
            )
            return shlex.split(template)
        if (
            self.BUILTIN_IDM_RUNNER.exists()
            and (self.settings.idm_vton_repo_path or self.settings.idm_vton_server_url)
        ):
            return [self.settings.idm_vton_python_bin, str(self.BUILTIN_IDM_RUNNER), str(request_json), str(output_dir)]
        if self.settings.vton_model_path and self.BUILTIN_DIFFUSERS_RUNNER.exists():
            return [sys.executable, str(self.BUILTIN_DIFFUSERS_RUNNER), str(request_json), str(output_dir)]
        return []

    def _runner_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.settings.vton_model_path:
            env["MIRA_STYLIST_VTON_MODEL_PATH"] = self.settings.vton_model_path
        env["MIRA_STYLIST_VTON_DEVICE"] = self.settings.vton_device
        env["MIRA_STYLIST_VTON_DTYPE"] = self.settings.vton_dtype
        env["MIRA_STYLIST_VTON_STEPS"] = str(self.settings.vton_num_inference_steps)
        env["MIRA_STYLIST_VTON_GUIDANCE_SCALE"] = str(self.settings.vton_guidance_scale)
        env["MIRA_STYLIST_VTON_STRENGTH"] = str(self.settings.vton_strength)
        if self.settings.idm_vton_repo_path:
            env["MIRA_STYLIST_IDM_VTON_REPO_PATH"] = self.settings.idm_vton_repo_path
        if self.settings.idm_vton_server_url:
            env["MIRA_STYLIST_IDM_VTON_SERVER_URL"] = self.settings.idm_vton_server_url
        env["MIRA_STYLIST_IDM_VTON_DENOISE_STEPS"] = str(self.settings.idm_vton_denoise_steps)
        env["MIRA_STYLIST_IDM_VTON_SEED"] = str(self.settings.idm_vton_seed)
        env["MIRA_STYLIST_IDM_VTON_AUTO_MASK"] = "true" if self.settings.idm_vton_auto_mask else "false"
        env["MIRA_STYLIST_IDM_VTON_AUTO_CROP"] = "true" if self.settings.idm_vton_auto_crop else "false"
        env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
        return env

    def _parse_runner_result(self, stdout: str, output_dir: Path) -> VTONRunResult | None:
        text = stdout.strip()
        candidate_paths: list[Path] = []
        if text:
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                return self._model_from_dict(payload)
        candidate_paths.append(output_dir / "result.json")
        for path in candidate_paths:
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                return self._model_from_dict(payload)
        return None

    @staticmethod
    def _write_result(storage_paths: TryOnStoragePaths, result: VTONRunResult) -> None:
        path = storage_paths.metadata_dir / "vton_result.json"
        path.write_text(json.dumps(VTONService._dump_model(result), indent=2, default=str), encoding="utf-8")

    @staticmethod
    def _dump_model(model) -> dict:
        if hasattr(model, "model_dump"):
            return model.model_dump()
        return model.dict()

    @staticmethod
    def _model_from_dict(payload: dict) -> VTONRunResult:
        if hasattr(VTONRunResult, "model_validate"):
            return VTONRunResult.model_validate(payload)
        return VTONRunResult.parse_obj(payload)

    @staticmethod
    def _resolve_browser_image(path: Path) -> Path | None:
        if not path.exists():
            return None
        mime = mimetypes.guess_type(str(path))[0] or ""
        if mime in {"image/heic", "image/heif"}:
            converted = path.with_suffix(".preview.jpg")
            if converted.exists():
                return converted
            return None
        return path
