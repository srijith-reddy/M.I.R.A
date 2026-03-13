from __future__ import annotations

from mira_stylist.models import StylistJobStatus

from .stylist_job_service import StylistJobService
from .tryon_service import TryOnPreviewService


class RemoteJobReconciler:
    """Poll unfinished remote pipeline jobs so preview state can recover after backend restarts."""

    def __init__(self, *, jobs: StylistJobService, tryon: TryOnPreviewService) -> None:
        self.jobs = jobs
        self.tryon = tryon

    def reconcile_pending_jobs(self) -> int:
        if not getattr(self.tryon.orchestrator.remote_provider.config, "base_url", ""):
            return 0
        reconciled = 0
        for job in self.jobs.list_jobs():
            if job.status not in {StylistJobStatus.GPU_QUEUED, StylistJobStatus.GPU_RUNNING, StylistJobStatus.PREPROCESSING}:
                continue
            updated = self.tryon.poll_pipeline_job(job.stylist_job_id)
            if updated is not None:
                reconciled += 1
        return reconciled
