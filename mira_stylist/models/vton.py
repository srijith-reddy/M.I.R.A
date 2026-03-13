from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from .common import utc_now


class VTONInputPayload(BaseModel):
    request_id: str
    avatar_id: str
    garment_id: str
    pose: str
    camera_angle: str
    avatar_image_path: str
    garment_image_path: str
    person_segmentation_path: Optional[str] = None
    person_segmentation_metadata_path: Optional[str] = None
    pose_metadata_path: Optional[str] = None
    garment_mask_path: Optional[str] = None
    garment_category: Optional[str] = None
    garment_color: Optional[str] = None
    garment_title: Optional[str] = None
    output_dir: str
    notes: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class VTONRunResult(BaseModel):
    status: str = "unavailable"
    backend: str = "none"
    generated_preview_path: Optional[str] = None
    generated_auxiliary_paths: Dict[str, str] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class RemoteArtifactRef(BaseModel):
    role: str
    path: str
    public_url: Optional[str] = None
    mime_type: Optional[str] = None
    notes: List[str] = Field(default_factory=list)


class RemoteTryOnRequest(BaseModel):
    schema_version: str = "runpod.v1"
    stylist_job_id: str
    preview_job_id: str
    provider: str = "catvton_remote"
    provider_version: str = "v1"
    callback_url: Optional[str] = None
    callback_token: Optional[str] = None
    render_mode: str
    camera_angle: str
    garment_category: Optional[str] = None
    artifact_manifest_path: Optional[str] = None
    artifacts: List[RemoteArtifactRef] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class RemoteTryOnAccepted(BaseModel):
    schema_version: str = "runpod.v1"
    status: str = "accepted"
    backend: str = "remote_gpu_api"
    provider_job_id: Optional[str] = None
    result_poll_url: Optional[str] = None
    worker_version: Optional[str] = None
    notes: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class RemoteTryOnStatus(BaseModel):
    schema_version: str = "runpod.v1"
    status: str = "queued"
    backend: str = "remote_gpu_api"
    provider_job_id: Optional[str] = None
    worker_version: Optional[str] = None
    result_image_url: Optional[str] = None
    result_image_base64: Optional[str] = None
    metadata: Dict[str, str] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
