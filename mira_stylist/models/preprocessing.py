from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from .common import utc_now


class ImageArtifact(BaseModel):
    role: str
    path: str
    mime_type: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    notes: List[str] = Field(default_factory=list)


class PoseArtifact(BaseModel):
    provider: str
    status: str = "unavailable"
    metadata_path: Optional[str] = None
    notes: List[str] = Field(default_factory=list)


class HumanParsingArtifact(BaseModel):
    provider: str
    status: str = "unavailable"
    mask_path: Optional[str] = None
    metadata_path: Optional[str] = None
    mask_type: Optional[str] = None
    notes: List[str] = Field(default_factory=list)


class GarmentSegmentationArtifact(BaseModel):
    provider: str
    status: str = "unavailable"
    mask_path: Optional[str] = None
    alpha_png_path: Optional[str] = None
    metadata_path: Optional[str] = None
    notes: List[str] = Field(default_factory=list)


class ClothAlignmentArtifact(BaseModel):
    provider: str = "pending"
    status: str = "not_run"
    aligned_image_path: Optional[str] = None
    metadata_path: Optional[str] = None
    notes: List[str] = Field(default_factory=list)


class TryOnArtifactManifest(BaseModel):
    manifest_id: str
    job_id: str
    avatar_id: str
    garment_id: str
    user_image: ImageArtifact
    garment_image: ImageArtifact
    pose: Optional[PoseArtifact] = None
    human_parsing: Optional[HumanParsingArtifact] = None
    garment_segmentation: Optional[GarmentSegmentationArtifact] = None
    cloth_alignment: Optional[ClothAlignmentArtifact] = None
    provider_hint: str = "remote_gpu_api"
    notes: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
