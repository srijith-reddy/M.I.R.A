from __future__ import annotations

from pathlib import Path

from mira_stylist.models import (
    StylistJobError,
    StylistJobStage,
    StylistJobStatus,
    StylistTryOnJob,
)
from mira_stylist.models.common import utc_now
from mira_stylist.utils.ids import new_prefixed_id
from mira_stylist.utils.paths import TryOnStoragePaths

from .storage_service import AssetStorageService


class StylistJobService:
    """Persist and reload pipeline-oriented try-on jobs for remote GPU orchestration."""

    def __init__(self, storage: AssetStorageService):
        self.storage = storage
        self._jobs: dict[str, StylistTryOnJob] = {}
        self._load_existing_jobs()

    def create_job(
        self,
        *,
        preview_job_id: str,
        avatar_id: str,
        garment_id: str,
        storage_paths: TryOnStoragePaths,
        provider: str = "remote_gpu_api",
    ) -> StylistTryOnJob:
        job = StylistTryOnJob(
            stylist_job_id=new_prefixed_id("stylistjob"),
            preview_job_id=preview_job_id,
            avatar_id=avatar_id,
            garment_id=garment_id,
            provider=provider,
            callback_token=new_prefixed_id("cbtoken"),
        )
        self._write_job(storage_paths, job)
        self._jobs[job.stylist_job_id] = job
        return job

    def update_job(
        self,
        *,
        job: StylistTryOnJob,
        storage_paths: TryOnStoragePaths,
        status: StylistJobStatus | None = None,
        stage: StylistJobStage | None = None,
        notes: list[str] | None = None,
        error: StylistJobError | None = None,
        artifact_manifest_path: str | None = None,
        provider_request_path: str | None = None,
        provider_response_path: str | None = None,
        provider_job_id: str | None = None,
        result_preview_path: str | None = None,
    ) -> StylistTryOnJob:
        if status is not None:
            job.status = status
        if stage is not None:
            job.stage = stage
        if notes:
            job.notes.extend(notes)
        if error:
            job.errors.append(error)
        if artifact_manifest_path is not None:
            job.artifact_manifest_path = artifact_manifest_path
        if provider_request_path is not None:
            job.provider_request_path = provider_request_path
        if provider_response_path is not None:
            job.provider_response_path = provider_response_path
        if provider_job_id is not None:
            job.provider_job_id = provider_job_id
        if result_preview_path is not None:
            job.result_preview_path = result_preview_path
        job.updated_at = utc_now()
        self._write_job(storage_paths, job)
        self._jobs[job.stylist_job_id] = job
        return job

    def get_job(self, stylist_job_id: str) -> StylistTryOnJob | None:
        if stylist_job_id in self._jobs:
            return self._jobs[stylist_job_id]
        matches = self.storage.glob(f"tryon/*/jobs/{stylist_job_id}.json")
        if not matches:
            return None
        job = self.storage.read_model(matches[0], StylistTryOnJob)
        if not job:
            return None
        self._jobs[stylist_job_id] = job
        return job

    def find_by_preview_job(self, preview_job_id: str) -> StylistTryOnJob | None:
        for job in self._jobs.values():
            if job.preview_job_id == preview_job_id:
                return job
        matches = self.storage.glob("tryon/*/jobs/*.json")
        for path in matches:
            job = self.storage.read_model(path, StylistTryOnJob)
            if job and job.preview_job_id == preview_job_id:
                self._jobs[job.stylist_job_id] = job
                return job
        return None

    def list_jobs(self) -> list[StylistTryOnJob]:
        return list(self._jobs.values())

    def _load_existing_jobs(self) -> None:
        for path in self.storage.glob("tryon/*/jobs/*.json"):
            job = self.storage.read_model(path, StylistTryOnJob)
            if job:
                self._jobs[job.stylist_job_id] = job

    def _write_job(self, storage_paths: TryOnStoragePaths, job: StylistTryOnJob) -> Path:
        return self.storage.write_metadata(storage_paths.jobs_dir / f"{job.stylist_job_id}.json", job)
