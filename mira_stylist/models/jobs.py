from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from .common import utc_now


class StylistJobStatus(str, Enum):
    CREATED = "created"
    PREPROCESSING = "preprocessing"
    GPU_QUEUED = "gpu_queued"
    GPU_RUNNING = "gpu_running"
    POSTPROCESSING = "postprocessing"
    COMPLETED = "completed"
    FAILED = "failed"


class StylistJobStage(str, Enum):
    INGEST = "ingest"
    PREPROCESS = "preprocess"
    INFER = "infer"
    POSTPROCESS = "postprocess"
    FINALIZE = "finalize"


class StylistJobError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class StylistTryOnJob(BaseModel):
    stylist_job_id: str
    preview_job_id: str
    avatar_id: str
    garment_id: str
    status: StylistJobStatus = StylistJobStatus.CREATED
    stage: StylistJobStage = StylistJobStage.INGEST
    provider: str = "remote_gpu_api"
    artifact_manifest_path: Optional[str] = None
    provider_request_path: Optional[str] = None
    provider_response_path: Optional[str] = None
    provider_job_id: Optional[str] = None
    callback_token: Optional[str] = None
    result_preview_path: Optional[str] = None
    errors: List[StylistJobError] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
