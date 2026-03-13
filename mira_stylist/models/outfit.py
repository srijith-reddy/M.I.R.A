from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from .common import PreviewStatus, utc_now
from .garment import GarmentCategory


class OutfitComponentSourceKind(str, Enum):
    ANCHOR_GARMENT = "anchor_garment"
    GENERATED_COMPANION = "generated_companion"


class OutfitComponent(BaseModel):
    component_id: str
    source_kind: OutfitComponentSourceKind
    source_garment_id: Optional[str] = None
    role: str
    category: GarmentCategory = GarmentCategory.UNKNOWN
    label: str
    color: Optional[str] = None
    layer_order: int = 0
    rationale: Optional[str] = None
    locked: bool = False


class GeneratedOutfit(BaseModel):
    outfit_id: str
    avatar_id: str
    anchor_garment_id: str
    occasion: Optional[str] = None
    style_goal: Optional[str] = None
    weather_hint: Optional[str] = None
    summary: str
    outfit_formula: List[str] = Field(default_factory=list)
    components: List[OutfitComponent] = Field(default_factory=list)
    confidence_label: str = "low"
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    preview_status: PreviewStatus = PreviewStatus.QUEUED
    output_asset_paths: Dict[str, str] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
