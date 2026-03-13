from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from .common import SourceType, utc_now


class AvatarStatus(str, Enum):
    SCAN_PENDING = "scan_pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    ARCHIVED = "archived"


class BodyMeasurements(BaseModel):
    height_cm: Optional[float] = None
    chest_cm: Optional[float] = None
    waist_cm: Optional[float] = None
    hips_cm: Optional[float] = None
    inseam_cm: Optional[float] = None
    shoulder_width_cm: Optional[float] = None
    body_shape_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: Optional[str] = None


class AvatarAssetManifest(BaseModel):
    mesh_path: Optional[str] = None
    texture_path: Optional[str] = None
    preview_image_path: Optional[str] = None
    side_preview_image_path: Optional[str] = None
    skeleton_path: Optional[str] = None
    measurements_path: Optional[str] = None
    front_capture_path: Optional[str] = None
    side_capture_path: Optional[str] = None
    body_profile_path: Optional[str] = None
    metadata_path: Optional[str] = None


class BodyProfile(BaseModel):
    shoulder_scale: float = Field(default=1.0, ge=0.7, le=1.35)
    waist_scale: float = Field(default=1.0, ge=0.7, le=1.3)
    hip_scale: float = Field(default=1.0, ge=0.75, le=1.35)
    torso_length_ratio: float = Field(default=0.36, ge=0.28, le=0.48)
    leg_length_ratio: float = Field(default=0.48, ge=0.38, le=0.62)
    depth_scale: float = Field(default=0.82, ge=0.6, le=1.2)
    body_frame: str = "regular"
    posture_hint: str = "neutral"
    profile_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: List[str] = Field(default_factory=list)


class ScanCaptureBundle(BaseModel):
    bundle_id: str
    scan_session_id: str
    upload_mode: str = "metadata_only"
    rgb_frame_count: int = 0
    depth_frame_count: int = 0
    lidar_point_count: Optional[int] = None
    image_resolution: Optional[str] = None
    depth_resolution: Optional[str] = None
    duration_ms: Optional[int] = None
    coverage_score: float = Field(default=0.0, ge=0.0, le=1.0)
    preview_image_path: Optional[str] = None
    bundle_reference: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ScanSession(BaseModel):
    scan_session_id: str
    user_id: str
    source_type: SourceType
    capture_device_model: Optional[str] = None
    has_lidar: bool = False
    frame_count: int = 0
    depth_frame_count: int = 0
    image_resolution: Optional[str] = None
    quality_score: float = Field(default=0.0, ge=0.0, le=1.0)
    status: AvatarStatus = AvatarStatus.SCAN_PENDING
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class UserAvatar(BaseModel):
    user_id: str
    avatar_id: str
    display_name: Optional[str] = None
    status: AvatarStatus
    source_type: SourceType
    scan_session_id: Optional[str] = None
    measurements: BodyMeasurements = Field(default_factory=BodyMeasurements)
    body_profile: BodyProfile = Field(default_factory=BodyProfile)
    assets: AvatarAssetManifest = Field(default_factory=AvatarAssetManifest)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
