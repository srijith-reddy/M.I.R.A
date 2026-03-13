from __future__ import annotations

from dataclasses import dataclass
import json

from mira_stylist.models import (
    RemoteTryOnAccepted,
    RemoteTryOnStatus,
    GarmentItem,
    StylistJobError,
    StylistJobStage,
    StylistJobStatus,
    StylistTryOnJob,
    TryOnArtifactManifest,
    TryOnRequest,
    UserAvatar,
    VTONRunResult,
)
from mira_stylist.models.common import model_dump_compat
from mira_stylist.utils.paths import TryOnStoragePaths
from mira_stylist.vton.providers import RemoteGPUVTONProvider

from .artifact_manifest_service import ArtifactManifestService
from .preprocessing_service import PreprocessingService
from .stylist_job_service import StylistJobService
from .vton_service import VTONService


@dataclass(frozen=True)
class OrchestratedTryOnRun:
    stylist_job: StylistTryOnJob
    artifact_manifest: TryOnArtifactManifest | None
    vton_result: VTONRunResult | None


class TryOnPipelineOrchestrator:
    """Stage production-oriented preprocessing and VTON execution without breaking the current API."""

    def __init__(
        self,
        *,
        jobs: StylistJobService,
        manifests: ArtifactManifestService,
        preprocessing: PreprocessingService,
        vton: VTONService,
        remote_provider: RemoteGPUVTONProvider | None = None,
    ) -> None:
        self.jobs = jobs
        self.manifests = manifests
        self.preprocessing = preprocessing
        self.vton = vton
        self.remote_provider = remote_provider or RemoteGPUVTONProvider()

    def run_sync(
        self,
        *,
        preview_job_id: str,
        request: TryOnRequest,
        avatar: UserAvatar,
        garment: GarmentItem,
        storage_paths: TryOnStoragePaths,
    ) -> OrchestratedTryOnRun:
        stylist_job = self.jobs.create_job(
            preview_job_id=preview_job_id,
            avatar_id=avatar.avatar_id,
            garment_id=garment.garment_id,
            storage_paths=storage_paths,
            provider="remote_gpu_api",
        )
        self.jobs.update_job(
            job=stylist_job,
            storage_paths=storage_paths,
            status=StylistJobStatus.PREPROCESSING,
            stage=StylistJobStage.PREPROCESS,
            notes=["Building preprocessing artifacts for staged remote-GPU-compatible try-on."],
        )

        payload = self.preprocessing.build_vton_payload(
            job_id=preview_job_id,
            request=request,
            avatar=avatar,
            garment=garment,
            storage_paths=storage_paths,
        )
        artifact_manifest = self.manifests.create_manifest(
            job_id=preview_job_id,
            request=request,
            avatar=avatar,
            garment=garment,
            payload=payload,
            storage_paths=storage_paths,
        )
        self.jobs.update_job(
            job=stylist_job,
            storage_paths=storage_paths,
            artifact_manifest_path=str(storage_paths.metadata_dir / "artifact_manifest.json"),
            status=StylistJobStatus.GPU_RUNNING,
            stage=StylistJobStage.INFER,
            notes=["Preprocessing artifacts are ready; dispatching to the configured VTON provider."],
        )

        vton_result = self.vton.generate_preview(
            job_id=preview_job_id,
            storage_paths=storage_paths,
            payload=payload,
        )

        if vton_result and vton_result.status == "ok":
            self.jobs.update_job(
                job=stylist_job,
                storage_paths=storage_paths,
                status=StylistJobStatus.COMPLETED,
                stage=StylistJobStage.FINALIZE,
                provider_response_path=str(storage_paths.metadata_dir / "vton_result.json"),
                result_preview_path=vton_result.generated_preview_path,
                notes=["Configured VTON provider returned a preview successfully."],
            )
        else:
            error = None
            status = StylistJobStatus.FAILED if payload is not None else StylistJobStatus.COMPLETED
            if vton_result:
                error = StylistJobError(
                    code=vton_result.status,
                    message="VTON provider did not return a usable preview.",
                    retryable=vton_result.status in {"runtime_error", "runner_error", "unavailable"},
                )
            elif payload is None:
                error = StylistJobError(
                    code="payload_unavailable",
                    message="Preprocessing could not build a VTON payload for this request.",
                    retryable=False,
                )
            self.jobs.update_job(
                job=stylist_job,
                storage_paths=storage_paths,
                status=status,
                stage=StylistJobStage.FINALIZE,
                provider_response_path=str(storage_paths.metadata_dir / "vton_result.json")
                if (storage_paths.metadata_dir / "vton_result.json").exists()
                else None,
                error=error,
                notes=[
                    "Current preview flow will continue with fallback rendering if the VTON provider does not return a result."
                ],
            )

        return OrchestratedTryOnRun(
            stylist_job=stylist_job,
            artifact_manifest=artifact_manifest,
            vton_result=vton_result,
        )

    def submit_async(
        self,
        *,
        preview_job_id: str,
        request: TryOnRequest,
        avatar: UserAvatar,
        garment: GarmentItem,
        storage_paths: TryOnStoragePaths,
    ) -> OrchestratedTryOnRun:
        stylist_job = self.jobs.create_job(
            preview_job_id=preview_job_id,
            avatar_id=avatar.avatar_id,
            garment_id=garment.garment_id,
            storage_paths=storage_paths,
            provider=self.remote_provider.provider_name,
        )
        self.jobs.update_job(
            job=stylist_job,
            storage_paths=storage_paths,
            status=StylistJobStatus.PREPROCESSING,
            stage=StylistJobStage.PREPROCESS,
            notes=["Preparing remote GPU artifact bundle for async try-on."],
        )
        payload = self.preprocessing.build_vton_payload(
            job_id=preview_job_id,
            request=request,
            avatar=avatar,
            garment=garment,
            storage_paths=storage_paths,
        )
        artifact_manifest = self.manifests.create_manifest(
            job_id=preview_job_id,
            request=request,
            avatar=avatar,
            garment=garment,
            payload=payload,
            storage_paths=storage_paths,
        )
        self.jobs.update_job(
            job=stylist_job,
            storage_paths=storage_paths,
            artifact_manifest_path=str(storage_paths.metadata_dir / "artifact_manifest.json"),
            status=StylistJobStatus.GPU_QUEUED,
            stage=StylistJobStage.INFER,
        )
        remote_request = self.remote_provider.build_remote_request(
            stylist_job=stylist_job,
            artifact_manifest=artifact_manifest,
            payload=payload,
        )
        request_path = storage_paths.jobs_dir / f"{stylist_job.stylist_job_id}_remote_request.json"
        request_path.write_text(json.dumps(model_dump_compat(remote_request), indent=2, default=str), encoding="utf-8")
        submit_result = self.remote_provider.submit(
            request=remote_request,
            output_dir=storage_paths.jobs_dir / stylist_job.stylist_job_id,
        )
        self.jobs.update_job(
            job=stylist_job,
            storage_paths=storage_paths,
            provider_request_path=str(request_path),
        )
        if isinstance(submit_result, RemoteTryOnAccepted):
            self.jobs.update_job(
                job=stylist_job,
                storage_paths=storage_paths,
                status=StylistJobStatus.GPU_QUEUED,
                stage=StylistJobStage.INFER,
                provider_job_id=submit_result.provider_job_id,
                notes=submit_result.notes,
            )
            return OrchestratedTryOnRun(stylist_job=stylist_job, artifact_manifest=artifact_manifest, vton_result=None)
        if submit_result.status == "ok":
            self.jobs.update_job(
                job=stylist_job,
                storage_paths=storage_paths,
                status=StylistJobStatus.COMPLETED,
                stage=StylistJobStage.FINALIZE,
                result_preview_path=submit_result.generated_preview_path,
                provider_response_path=str(storage_paths.jobs_dir / stylist_job.stylist_job_id / "remote_submit_response.json"),
                notes=submit_result.notes,
            )
        else:
            self.jobs.update_job(
                job=stylist_job,
                storage_paths=storage_paths,
                status=StylistJobStatus.FAILED,
                stage=StylistJobStage.INFER,
                error=StylistJobError(
                    code=submit_result.status,
                    message="Remote GPU provider did not accept or complete the job.",
                    retryable=submit_result.status in {"runtime_error", "unavailable"},
                ),
                provider_response_path=str(storage_paths.jobs_dir / stylist_job.stylist_job_id / "remote_submit_response.json"),
                notes=submit_result.notes,
            )
        return OrchestratedTryOnRun(stylist_job=stylist_job, artifact_manifest=artifact_manifest, vton_result=submit_result)

    def poll_async(
        self,
        *,
        stylist_job: StylistTryOnJob,
        storage_paths: TryOnStoragePaths,
    ) -> RemoteTryOnStatus | None:
        if not stylist_job.provider_job_id:
            return None
        status = self.remote_provider.poll(
            provider_job_id=stylist_job.provider_job_id,
            output_dir=storage_paths.jobs_dir / stylist_job.stylist_job_id,
        )
        self._apply_remote_status(stylist_job=stylist_job, storage_paths=storage_paths, status=status)
        return status

    def apply_callback(
        self,
        *,
        stylist_job: StylistTryOnJob,
        storage_paths: TryOnStoragePaths,
        status: RemoteTryOnStatus,
    ) -> StylistTryOnJob:
        return self._apply_remote_status(stylist_job=stylist_job, storage_paths=storage_paths, status=status)

    def _apply_remote_status(
        self,
        *,
        stylist_job: StylistTryOnJob,
        storage_paths: TryOnStoragePaths,
        status: RemoteTryOnStatus,
    ) -> StylistTryOnJob:
        response_path = storage_paths.jobs_dir / stylist_job.stylist_job_id / "remote_status.json"
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_text(json.dumps(model_dump_compat(status), indent=2, default=str), encoding="utf-8")
        if status.status in {"queued", "running", "processing"}:
            return self.jobs.update_job(
                job=stylist_job,
                storage_paths=storage_paths,
                status=StylistJobStatus.GPU_RUNNING if status.status != "queued" else StylistJobStatus.GPU_QUEUED,
                stage=StylistJobStage.INFER,
                provider_response_path=str(response_path),
                notes=status.notes,
            )
        if status.status == "completed":
            return self.jobs.update_job(
                job=stylist_job,
                storage_paths=storage_paths,
                status=StylistJobStatus.COMPLETED,
                stage=StylistJobStage.FINALIZE,
                provider_response_path=str(response_path),
                result_preview_path=status.result_image_url,
                notes=status.notes,
            )
        return self.jobs.update_job(
            job=stylist_job,
            storage_paths=storage_paths,
            status=StylistJobStatus.FAILED,
            stage=StylistJobStage.INFER,
            provider_response_path=str(response_path),
            error=StylistJobError(
                code=status.status,
                message="Remote GPU worker reported a non-success terminal state.",
                retryable=status.status in {"timeout", "runtime_error"},
            ),
            notes=status.notes,
        )
