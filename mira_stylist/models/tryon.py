from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from .common import CameraAngle, PreviewStatus, RenderMode, utc_now


class FitAssessment(BaseModel):
    fit_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: List[str] = Field(default_factory=list)
    estimated_size_alignment: Optional[str] = None
    occlusion_risk: Optional[str] = None


class StylistCommentary(BaseModel):
    summary: str
    what_works: List[str] = Field(default_factory=list)
    watch_outs: List[str] = Field(default_factory=list)
    fit_caveats: List[str] = Field(default_factory=list)
    confidence_label: str = "low"
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    tone: str = "balanced"
    notes: List[str] = Field(default_factory=list)


class SingleLookFeedback(BaseModel):
    job_id: str
    question: Optional[str] = None
    occasion: Optional[str] = None
    answer: str
    confidence_label: str = "low"
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    supporting_points: List[str] = Field(default_factory=list)
    cautions: List[str] = Field(default_factory=list)
    follow_up_suggestions: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class LookComparisonFeedback(BaseModel):
    primary_job_id: str
    secondary_job_id: str
    occasion: Optional[str] = None
    style_goal: Optional[str] = None
    winner_job_id: str
    verdict: str
    confidence_label: str = "low"
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    primary_strengths: List[str] = Field(default_factory=list)
    secondary_strengths: List[str] = Field(default_factory=list)
    decision_factors: List[str] = Field(default_factory=list)
    cautions: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class PairingRecommendation(BaseModel):
    role: str
    suggested_category: str
    suggestion: str
    colors: List[str] = Field(default_factory=list)
    rationale: str
    priority: str = "medium"


class PairingSuggestion(BaseModel):
    avatar_id: str
    garment_id: str
    occasion: Optional[str] = None
    style_goal: Optional[str] = None
    weather_hint: Optional[str] = None
    summary: str
    outfit_formula: List[str] = Field(default_factory=list)
    recommendations: List[PairingRecommendation] = Field(default_factory=list)
    confidence_label: str = "low"
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: List[str] = Field(default_factory=list)


class TryOnRequest(BaseModel):
    avatar_id: str
    garment_id: str
    pose: str = "neutral"
    camera_angle: CameraAngle = CameraAngle.FRONT
    render_mode: RenderMode = RenderMode.STYLED_OVERLAY
    notes: Optional[str] = None


class TryOnResult(BaseModel):
    job_id: str
    avatar_id: str
    garment_id: str
    preview_status: PreviewStatus = PreviewStatus.QUEUED
    fit_assessment: FitAssessment = Field(default_factory=FitAssessment)
    stylist_commentary: Optional[StylistCommentary] = None
    output_asset_paths: Dict[str, str] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PreviewRenderJob(BaseModel):
    job_id: str
    request: TryOnRequest
    preview_status: PreviewStatus = PreviewStatus.QUEUED
    queued_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    result: Optional[TryOnResult] = None
