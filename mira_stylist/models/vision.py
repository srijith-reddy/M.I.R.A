from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from .common import utc_now


class VisionKeypoint(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class VisionBodyAnalysis(BaseModel):
    status: str = "unavailable"
    provider: str = "apple_vision_body_pose"
    view: str = "front"
    image_path: Optional[str] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    points: Dict[str, VisionKeypoint] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class VisionSegmentationAnalysis(BaseModel):
    status: str = "unavailable"
    provider: str = "apple_vision_person_segmentation"
    view: str = "front"
    image_path: Optional[str] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    mask_path: Optional[str] = None
    bbox_x: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    bbox_y: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    bbox_width: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    bbox_height: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    coverage_score: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
