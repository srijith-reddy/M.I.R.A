from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from mira_stylist.models.vton import VTONInputPayload, VTONRunResult

from .remote_vton_client import RemoteVTONClient, RemoteVTONConfig


@dataclass(frozen=True)
class CatVTONConfig:
    mode: str = field(default_factory=lambda: os.getenv("MIRA_STYLIST_CATVTON_MODE", "remote_gpu_api").strip() or "remote_gpu_api")
    repo_path: str = field(
        default_factory=lambda: os.getenv(
            "MIRA_STYLIST_CATVTON_REPO_PATH",
            str((Path.cwd() / "third_party" / "CatVTON").resolve()) if (Path.cwd() / "third_party" / "CatVTON").exists() else "",
        ).strip()
    )
    python_bin: str = field(default_factory=lambda: os.getenv("MIRA_STYLIST_CATVTON_PYTHON_BIN", sys.executable).strip() or sys.executable)
    runner_command: str = field(default_factory=lambda: os.getenv("MIRA_STYLIST_CATVTON_RUNNER", "").strip())
    base_model_path: str = field(
        default_factory=lambda: os.getenv("MIRA_STYLIST_CATVTON_BASE_MODEL_PATH", "booksforcharlie/stable-diffusion-inpainting").strip()
    )
    resume_path: str = field(default_factory=lambda: os.getenv("MIRA_STYLIST_CATVTON_RESUME_PATH", "zhengchong/CatVTON").strip())
    attn_version: str = field(default_factory=lambda: os.getenv("MIRA_STYLIST_CATVTON_ATTN_VERSION", "mix").strip() or "mix")
    mixed_precision: str = field(default_factory=lambda: os.getenv("MIRA_STYLIST_CATVTON_MIXED_PRECISION", "no").strip() or "no")
    width: int = field(default_factory=lambda: int(os.getenv("MIRA_STYLIST_CATVTON_WIDTH", "768")))
    height: int = field(default_factory=lambda: int(os.getenv("MIRA_STYLIST_CATVTON_HEIGHT", "1024")))
    num_inference_steps: int = field(default_factory=lambda: int(os.getenv("MIRA_STYLIST_CATVTON_STEPS", "30")))
    guidance_scale: float = field(default_factory=lambda: float(os.getenv("MIRA_STYLIST_CATVTON_GUIDANCE_SCALE", "2.5")))
    repaint: bool = field(
        default_factory=lambda: os.getenv("MIRA_STYLIST_CATVTON_REPAINT", "true").strip().lower() not in {"0", "false", "no"}
    )
    seed: int = field(default_factory=lambda: int(os.getenv("MIRA_STYLIST_CATVTON_SEED", "42")))
    timeout_seconds: int = field(default_factory=lambda: int(os.getenv("MIRA_STYLIST_CATVTON_TIMEOUT_SECONDS", "180")))
    remote_base_url: str = field(default_factory=lambda: os.getenv("MIRA_STYLIST_REMOTE_VTON_URL", "").strip())
    remote_api_path: str = field(default_factory=lambda: os.getenv("MIRA_STYLIST_REMOTE_VTON_API_PATH", "/tryon").strip() or "/tryon")


class CatVTONEngine:
    """Default CatVTON-backed try-on engine with local and remote modes."""

    LOCAL_RUNNER = Path(__file__).resolve().parents[1] / "tools" / "catvton_local_runner.py"

    def __init__(self, config: CatVTONConfig | None = None) -> None:
        self.config = config or CatVTONConfig()
        self.remote_client = RemoteVTONClient(
            RemoteVTONConfig(
                base_url=self.config.remote_base_url,
                api_path=self.config.remote_api_path,
                timeout_seconds=self.config.timeout_seconds,
            )
        )

    def is_enabled(self) -> bool:
        mode = self.config.mode.lower()
        if mode == "remote_gpu_api":
            return bool(self.config.remote_base_url)
        return bool(self.config.repo_path or self.config.runner_command)

    def run(self, *, payload: VTONInputPayload, output_dir: str | Path) -> VTONRunResult | None:
        mode = self.config.mode.lower()
        if not self.is_enabled():
            return None
        if mode == "remote_gpu_api":
            return self.remote_client.run(payload=payload, output_dir=output_dir)
        return self._run_local(payload=payload, output_dir=output_dir)

    def _run_local(self, *, payload: VTONInputPayload, output_dir: str | Path) -> VTONRunResult:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        request_path = output_dir / "catvton_request.json"
        request_payload = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
        request_path.write_text(json.dumps(request_payload, indent=2, default=str), encoding="utf-8")
        command = self._render_command(request_path=request_path, output_dir=output_dir)
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.config.timeout_seconds,
                env=self._runner_env(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return VTONRunResult(
                status="runner_error",
                backend="catvton",
                notes=[f"Failed to execute CatVTON runner: {exc}"],
            )
        payload = self._extract_result_payload(completed.stdout)
        try:
            if payload is None:
                raise json.JSONDecodeError("No JSON object found in runner output.", completed.stdout, 0)
            result = VTONRunResult.model_validate(payload) if hasattr(VTONRunResult, "model_validate") else VTONRunResult.parse_obj(payload)
        except (json.JSONDecodeError, TypeError, ValueError):
            result = VTONRunResult(
                status="invalid_runner_output",
                backend="catvton",
                notes=[
                    completed.stderr.strip() or completed.stdout.strip() or "CatVTON runner did not emit valid JSON.",
                ],
            )
        if completed.returncode != 0:
            result.notes.append(f"Runner exited with code {completed.returncode}.")
        return result

    def _extract_result_payload(self, stdout: str) -> dict[str, object] | None:
        """CatVTON may print progress bars before the final JSON payload."""

        text = stdout.strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
        for line in reversed([line.strip() for line in text.splitlines() if line.strip()]):
            if not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return None

    def _render_command(self, *, request_path: Path, output_dir: Path) -> list[str]:
        if self.config.runner_command:
            template = self.config.runner_command.format(request_json=str(request_path), output_dir=str(output_dir))
            return shlex.split(template)
        return [self.config.python_bin, str(self.LOCAL_RUNNER), str(request_path), str(output_dir)]

    def _runner_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["MIRA_STYLIST_CATVTON_MODE"] = self.config.mode
        env["MIRA_STYLIST_CATVTON_REPO_PATH"] = self.config.repo_path
        env["MIRA_STYLIST_CATVTON_BASE_MODEL_PATH"] = self.config.base_model_path
        env["MIRA_STYLIST_CATVTON_RESUME_PATH"] = self.config.resume_path
        env["MIRA_STYLIST_CATVTON_ATTN_VERSION"] = self.config.attn_version
        env["MIRA_STYLIST_CATVTON_MIXED_PRECISION"] = self.config.mixed_precision
        env["MIRA_STYLIST_CATVTON_WIDTH"] = str(self.config.width)
        env["MIRA_STYLIST_CATVTON_HEIGHT"] = str(self.config.height)
        env["MIRA_STYLIST_CATVTON_STEPS"] = str(self.config.num_inference_steps)
        env["MIRA_STYLIST_CATVTON_GUIDANCE_SCALE"] = str(self.config.guidance_scale)
        env["MIRA_STYLIST_CATVTON_REPAINT"] = "true" if self.config.repaint else "false"
        env["MIRA_STYLIST_CATVTON_SEED"] = str(self.config.seed)
        env.setdefault("MIRA_STYLIST_CATVTON_SKIP_SAFETY_CHECK", "false")
        env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
        return env
