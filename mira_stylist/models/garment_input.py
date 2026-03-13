from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl

from .common import utc_now


class GarmentInputType(str, Enum):
    UPLOADED_IMAGE = "uploaded_image"
    PASTED_IMAGE = "pasted_image"
    SCREENSHOT = "screenshot"
    IMAGE_URL = "image_url"
    PRODUCT_PAGE_URL = "product_page_url"


class GarmentInputStatus(str, Enum):
    RECEIVED = "received"
    NORMALIZED = "normalized"
    CANDIDATE_REVIEW_REQUIRED = "candidate_review_required"
    SELECTED = "selected"
    FAILED = "failed"


class ProductSourceMetadata(BaseModel):
    source_url: Optional[HttpUrl] = None
    referring_page_url: Optional[HttpUrl] = None
    discovered_image_urls: List[HttpUrl] = Field(default_factory=list)
    page_title: Optional[str] = None
    domain: Optional[str] = None
    parser_name: Optional[str] = None
    parser_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: Optional[str] = None


class SourceImageRef(BaseModel):
    image_id: str
    source_url: Optional[HttpUrl] = None
    local_path: Optional[str] = None
    original_filename: Optional[str] = None
    mime_type: Optional[str] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    role: str = "raw_source"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: Optional[str] = None


class GarmentCandidateImage(BaseModel):
    candidate_id: str
    source_image_id: str
    local_preview_path: Optional[str] = None
    crop_hint: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: Optional[str] = None
    requires_user_confirmation: bool = True


class GarmentSelection(BaseModel):
    input_id: str
    selected_candidate_id: str
    selected_source_image_id: Optional[str] = None
    selection_notes: Optional[str] = None
    selected_at: datetime = Field(default_factory=utc_now)


class GarmentInput(BaseModel):
    input_id: str
    input_type: GarmentInputType
    original_filename: Optional[str] = None
    source_url: Optional[HttpUrl] = None
    referring_page_url: Optional[HttpUrl] = None
    supplemental_image_urls: List[HttpUrl] = Field(default_factory=list)
    mime_type: Optional[str] = None
    uploaded_by: str
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    file_size_bytes: Optional[int] = None
    content_sha256: Optional[str] = None
    raw_asset_path: Optional[str] = None
    normalized_asset_path: Optional[str] = None
    notes: Optional[str] = None
    status: GarmentInputStatus = GarmentInputStatus.RECEIVED
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class GarmentIngestionRequest(BaseModel):
    input_id: str
    uploaded_by: str
    title: Optional[str] = None
    brand: Optional[str] = None
    category_hint: Optional[str] = None
    color: Optional[str] = None
    size_info: Dict[str, str] = Field(default_factory=dict)
    notes: Optional[str] = None


class GarmentIngestionResult(BaseModel):
    input_id: str
    status: GarmentInputStatus
    garment_id: Optional[str] = None
    source_metadata: Optional[ProductSourceMetadata] = None
    source_images: List[SourceImageRef] = Field(default_factory=list)
    candidate_images: List[GarmentCandidateImage] = Field(default_factory=list)
    primary_candidate_id: Optional[str] = None
    selected_candidate_id: Optional[str] = None
    confidence_scores: Dict[str, float] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
