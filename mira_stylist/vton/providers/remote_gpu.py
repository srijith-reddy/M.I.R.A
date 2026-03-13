from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib import request as urlrequest

from mira_stylist.models import (
    RemoteArtifactRef,
    RemoteTryOnAccepted,
    RemoteTryOnRequest,
    RemoteTryOnStatus,
    StylistTryOnJob,
    TryOnArtifactManifest,
    VTONInputPayload,
    VTONRunResult,
)
from mira_stylist.services.object_store_service import ObjectStoreService

from .base import VTONProvider


@dataclass(frozen=True)
class RemoteGPUProviderConfig:
    base_url: str = field(default_factory=lambda: os.getenv("MIRA_STYLIST_REMOTE_VTON_URL", "").strip())
    submit_path: str = field(default_factory=lambda: os.getenv("MIRA_STYLIST_REMOTE_VTON_API_PATH", "/tryon/jobs").strip() or "/tryon/jobs")
    poll_path_template: str = field(
        default_factory=lambda: os.getenv("MIRA_STYLIST_REMOTE_VTON_POLL_PATH_TEMPLATE", "/tryon/jobs/{provider_job_id}").strip()
        or "/tryon/jobs/{provider_job_id}"
    )
    timeout_seconds: int = field(default_factory=lambda: int(os.getenv("MIRA_STYLIST_REMOTE_VTON_TIMEOUT_SECONDS", "120")))
    callback_url: str = field(default_factory=lambda: os.getenv("MIRA_STYLIST_REMOTE_VTON_CALLBACK_URL", "").strip())
    mode: str = field(default_factory=lambda: os.getenv("MIRA_STYLIST_REMOTE_VTON_MODE", "sync").strip() or "sync")


class RemoteGPUVTONProvider(VTONProvider):
    provider_name = "remote_gpu_api"

    def __init__(self, config: RemoteGPUProviderConfig | None = None, object_store: ObjectStoreService | None = None) -> None:
        self.config = config or RemoteGPUProviderConfig()
        self.object_store = object_store or ObjectStoreService()

    def build_remote_request(
        self,
        *,
        stylist_job: StylistTryOnJob,
        artifact_manifest: TryOnArtifactManifest,
        payload: VTONInputPayload | None,
    ) -> RemoteTryOnRequest:
        artifacts: list[RemoteArtifactRef] = []
        artifacts.append(self._artifact_ref("user_image", artifact_manifest.user_image.path, artifact_manifest.user_image.mime_type))
        artifacts.append(self._artifact_ref("garment_image", artifact_manifest.garment_image.path, artifact_manifest.garment_image.mime_type))
        if artifact_manifest.pose and artifact_manifest.pose.metadata_path:
            artifacts.append(self._artifact_ref("pose_metadata", artifact_manifest.pose.metadata_path, "application/json"))
        if artifact_manifest.human_parsing and artifact_manifest.human_parsing.mask_path:
            artifacts.append(self._artifact_ref("human_mask", artifact_manifest.human_parsing.mask_path, "image/png"))
        if artifact_manifest.human_parsing and artifact_manifest.human_parsing.metadata_path:
            artifacts.append(self._artifact_ref("human_mask_metadata", artifact_manifest.human_parsing.metadata_path, "application/json"))
        if artifact_manifest.garment_segmentation and artifact_manifest.garment_segmentation.mask_path:
            artifacts.append(self._artifact_ref("garment_mask", artifact_manifest.garment_segmentation.mask_path, "image/png"))
        if artifact_manifest.garment_segmentation and artifact_manifest.garment_segmentation.alpha_png_path:
            artifacts.append(self._artifact_ref("garment_alpha", artifact_manifest.garment_segmentation.alpha_png_path, "image/png"))
        callback_url = None
        if self.config.callback_url:
            callback_url = self.config.callback_url.format(stylist_job_id=stylist_job.stylist_job_id)
        return RemoteTryOnRequest(
            stylist_job_id=stylist_job.stylist_job_id,
            preview_job_id=stylist_job.preview_job_id,
            provider="catvton_remote",
            callback_url=callback_url,
            callback_token=stylist_job.callback_token,
            render_mode="styled_overlay",
            camera_angle="front",
            garment_category=payload.garment_category if payload else None,
            artifact_manifest_path=stylist_job.artifact_manifest_path,
            artifacts=artifacts,
            notes=[
                "Artifact-reference contract for remote GPU try-on worker.",
                "The GPU worker is expected to fetch artifacts and return either sync output or async job acceptance.",
            ],
        )

    def submit(
        self,
        *,
        request: RemoteTryOnRequest,
        output_dir: str | Path,
    ) -> RemoteTryOnAccepted | VTONRunResult:
        if not self.config.base_url:
            return VTONRunResult(status="unavailable", backend=self.provider_name, notes=["Remote GPU base URL is not configured."])
        endpoint = self.config.base_url.rstrip("/") + self.config.submit_path
        body = json.dumps(self._dump(request)).encode("utf-8")
        req = urlrequest.Request(endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlrequest.urlopen(req, timeout=self.config.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            return VTONRunResult(status="runtime_error", backend=self.provider_name, notes=[f"Remote GPU submit failed: {exc}"])
        if payload.get("status") == "accepted" or self.config.mode == "async":
            accepted = self._load_model(payload, RemoteTryOnAccepted)
            self._write_json(Path(output_dir) / "remote_submit_response.json", payload)
            return accepted
        result = self._sync_result_from_payload(payload, output_dir)
        self._write_json(Path(output_dir) / "remote_submit_response.json", payload)
        return result

    def poll(self, *, provider_job_id: str, output_dir: str | Path) -> RemoteTryOnStatus:
        if not self.config.base_url:
            return RemoteTryOnStatus(status="unavailable", backend=self.provider_name, notes=["Remote GPU base URL is not configured."])
        endpoint = self.config.base_url.rstrip("/") + self.config.poll_path_template.format(provider_job_id=provider_job_id)
        req = urlrequest.Request(endpoint, method="GET")
        try:
            with urlrequest.urlopen(req, timeout=self.config.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            return RemoteTryOnStatus(status="runtime_error", backend=self.provider_name, provider_job_id=provider_job_id, notes=[f"Remote GPU poll failed: {exc}"])
        self._write_json(Path(output_dir) / "remote_poll_response.json", payload)
        return self._load_model(payload, RemoteTryOnStatus)

    def _artifact_ref(self, role: str, path: str, mime_type: str | None) -> RemoteArtifactRef:
        public_url = self.object_store.publish_artifact(path)
        return RemoteArtifactRef(role=role, path=path, public_url=public_url, mime_type=mime_type)

    def _sync_result_from_payload(self, payload: dict, output_dir: str | Path) -> VTONRunResult:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        image_bytes = None
        if payload.get("result_image_base64"):
            try:
                image_bytes = base64.b64decode(payload["result_image_base64"])
            except Exception:
                image_bytes = None
        result_path = output_dir / "remote_tryon_result.png"
        if image_bytes:
            result_path.write_bytes(image_bytes)
            return VTONRunResult(
                status="ok",
                backend=self.provider_name,
                generated_preview_path=str(result_path),
                generated_auxiliary_paths={"remote_response": str(output_dir / "remote_submit_response.json")},
                notes=["Remote GPU provider returned a synchronous result image."],
            )
        return VTONRunResult(
            status=payload.get("status", "invalid_runner_output"),
            backend=self.provider_name,
            notes=payload.get("notes", ["Remote GPU response did not contain a usable result image."]),
        )

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def _dump(model) -> dict:
        if hasattr(model, "model_dump"):
            return model.model_dump(mode="json")
        return model.dict()

    @staticmethod
    def _load_model(payload: dict, model_type):
        if hasattr(model_type, "model_validate"):
            return model_type.model_validate(payload)
        return model_type.parse_obj(payload)
