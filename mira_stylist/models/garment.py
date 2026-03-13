from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl

from .common import utc_now
from .garment_input import ProductSourceMetadata, SourceImageRef


class GarmentCategory(str, Enum):
    TOP = "top"
    BOTTOM = "bottom"
    DRESS = "dress"
    OUTERWEAR = "outerwear"
    FOOTWEAR = "footwear"
    ACCESSORY = "accessory"
    UNKNOWN = "unknown"


class GarmentProcessingStatus(str, Enum):
    INGESTED = "ingested"
    EXTRACTING = "extracting"
    SEGMENTATION_PENDING = "segmentation_pending"
    READY = "ready"
    FAILED = "failed"


class ProductSource(BaseModel):
    source_url: Optional[HttpUrl] = None
    referring_page_url: Optional[HttpUrl] = None
    domain: Optional[str] = None
    brand: Optional[str] = None
    title: Optional[str] = None
    source_images: List[SourceImageRef] = Field(default_factory=list)
    scraped_at: datetime = Field(default_factory=utc_now)
    parser_notes: Optional[str] = None
    metadata: Optional[ProductSourceMetadata] = None


class GarmentAssetManifest(BaseModel):
    raw_asset_dir: Optional[str] = None
    candidates_dir: Optional[str] = None
    primary_image_path: Optional[str] = None
    segmented_asset_path: Optional[str] = None
    mesh_path: Optional[str] = None
    texture_path: Optional[str] = None
    preview_image_path: Optional[str] = None
    metadata_path: Optional[str] = None


class GarmentItem(BaseModel):
    garment_id: str
    raw_input_id: str
    source: ProductSource
    brand: Optional[str] = None
    title: str
    category: GarmentCategory = GarmentCategory.UNKNOWN
    color: Optional[str] = None
    size_info: Dict[str, str] = Field(default_factory=dict)
    primary_image_path: Optional[str] = None
    source_images: List[SourceImageRef] = Field(default_factory=list)
    assets: GarmentAssetManifest = Field(default_factory=GarmentAssetManifest)
    extraction_status: GarmentProcessingStatus = GarmentProcessingStatus.INGESTED
    confidence_scores: Dict[str, float] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
