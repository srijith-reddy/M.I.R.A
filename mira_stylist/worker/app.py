from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Dict

from fastapi import FastAPI, HTTPException

from mira_stylist.models import RemoteTryOnAccepted, RemoteTryOnRequest, RemoteTryOnStatus


@dataclass
class WorkerState:
    jobs: Dict[str, RemoteTryOnStatus]


@lru_cache(maxsize=1)
def get_state() -> WorkerState:
    return WorkerState(jobs={})


def create_worker_app() -> FastAPI:
    app = FastAPI(
        title="MIRA Stylist GPU Worker Stub",
        version="0.1.0",
        description="RunPod-style worker stub for remote VTON job submission and polling.",
    )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": "mira_stylist_gpu_worker_stub", "version": "0.1.0"}

    @app.post("/v1/jobs", response_model=RemoteTryOnAccepted)
    def submit_job(request: RemoteTryOnRequest) -> RemoteTryOnAccepted:
        state = get_state()
        provider_job_id = f"worker_{request.stylist_job_id}"
        state.jobs[provider_job_id] = RemoteTryOnStatus(
            status="queued",
            backend="remote_gpu_api",
            provider_job_id=provider_job_id,
            worker_version="stub.v1",
            metadata={"schema_version": request.schema_version, "provider": request.provider},
            notes=[
                "Worker stub accepted the job.",
                "Replace this stub with real CatVTON/RunPod inference.",
            ],
        )
        return RemoteTryOnAccepted(
            status="accepted",
            backend="remote_gpu_api",
            provider_job_id=provider_job_id,
            result_poll_url=f"/v1/jobs/{provider_job_id}",
            worker_version="stub.v1",
            notes=["Worker stub accepted the try-on job for asynchronous processing."],
        )

    @app.get("/v1/jobs/{provider_job_id}", response_model=RemoteTryOnStatus)
    def get_job(provider_job_id: str) -> RemoteTryOnStatus:
        state = get_state()
        if provider_job_id not in state.jobs:
            raise HTTPException(status_code=404, detail="Worker job not found.")
        return state.jobs[provider_job_id]

    @app.post("/v1/jobs/{provider_job_id}/complete", response_model=RemoteTryOnStatus)
    def complete_job(provider_job_id: str, payload: RemoteTryOnStatus) -> RemoteTryOnStatus:
        state = get_state()
        if provider_job_id not in state.jobs:
            raise HTTPException(status_code=404, detail="Worker job not found.")
        payload.provider_job_id = provider_job_id
        state.jobs[provider_job_id] = payload
        return payload

    return app


app = create_worker_app()
