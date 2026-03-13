from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl

from .avatar import BodyMeasurements
from .common import CameraAngle, RenderMode, SourceType
from .garment import GarmentCategory
from .garment_input import GarmentSelection
from .vton import RemoteTryOnStatus


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class CreateScanSessionRequest(BaseModel):
    user_id: str
    source_type: SourceType
    capture_device_model: Optional[str] = None
    has_lidar: bool = False
    frame_count: int = 0
    depth_frame_count: int = 0
    image_resolution: Optional[str] = None
    notes: Optional[str] = None


class CreateAvatarRequest(BaseModel):
    user_id: str
    scan_session_id: Optional[str] = None
    source_type: SourceType = SourceType.IMAGE_ESTIMATED
    display_name: Optional[str] = None
    measurements_override: Optional[BodyMeasurements] = None


class AvatarPhotoCaptureRequest(BaseModel):
    user_id: str
    display_name: Optional[str] = None
    front_image_base64: str
    side_image_base64: str
    front_original_filename: Optional[str] = None
    side_original_filename: Optional[str] = None
    front_mime_type: Optional[str] = None
    side_mime_type: Optional[str] = None
    height_cm: Optional[float] = None
    measurements_hint: Optional[BodyMeasurements] = None
    notes: Optional[str] = None


class QuickTryOnAvatarRequest(BaseModel):
    user_id: str
    display_name: Optional[str] = None
    image_base64: str
    original_filename: Optional[str] = None
    mime_type: Optional[str] = None
    height_cm: Optional[float] = None
    notes: Optional[str] = None


class ScanBetaSessionRequest(BaseModel):
    user_id: str
    display_name: Optional[str] = None
    capture_device_model: Optional[str] = None
    has_lidar: bool = True
    source_type: SourceType = SourceType.LIDAR
    expected_frame_count: int = 120
    expected_depth_frame_count: int = 80
    image_resolution: Optional[str] = None
    notes: Optional[str] = None


class ScanBetaCaptureBundleRequest(BaseModel):
    upload_mode: str = "metadata_only"
    rgb_frame_count: int = 0
    depth_frame_count: int = 0
    lidar_point_count: Optional[int] = None
    image_resolution: Optional[str] = None
    depth_resolution: Optional[str] = None
    duration_ms: Optional[int] = None
    coverage_hint: float = Field(default=0.0, ge=0.0, le=1.0)
    preview_image_base64: Optional[str] = None
    preview_original_filename: Optional[str] = None
    preview_mime_type: Optional[str] = None
    bundle_reference: Optional[str] = None
    notes: Optional[str] = None


class ScanBetaBuildRequest(BaseModel):
    scan_session_id: str
    display_name: Optional[str] = None
    height_cm: Optional[float] = None
    measurements_hint: Optional[BodyMeasurements] = None
    notes: Optional[str] = None


class ImageUrlIngestRequest(BaseModel):
    uploaded_by: str
    image_url: HttpUrl
    referring_page_url: Optional[HttpUrl] = None
    brand: Optional[str] = None
    title: Optional[str] = None
    category_hint: Optional[GarmentCategory] = None
    color: Optional[str] = None
    size_info: Dict[str, str] = Field(default_factory=dict)
    notes: Optional[str] = None


class PastedImageIngestRequest(BaseModel):
    uploaded_by: str
    image_base64: str
    original_filename: Optional[str] = None
    mime_type: Optional[str] = None
    referring_page_url: Optional[HttpUrl] = None
    brand: Optional[str] = None
    title: Optional[str] = None
    category_hint: Optional[GarmentCategory] = None
    color: Optional[str] = None
    size_info: Dict[str, str] = Field(default_factory=dict)
    notes: Optional[str] = None


class ScreenshotIngestRequest(BaseModel):
    uploaded_by: str
    image_base64: str
    original_filename: Optional[str] = None
    mime_type: Optional[str] = None
    referring_page_url: Optional[HttpUrl] = None
    brand: Optional[str] = None
    title: Optional[str] = None
    category_hint: Optional[GarmentCategory] = None
    color: Optional[str] = None
    size_info: Dict[str, str] = Field(default_factory=dict)
    notes: Optional[str] = None


class ProductPageUrlIngestRequest(BaseModel):
    uploaded_by: str
    product_page_url: HttpUrl
    image_urls: List[HttpUrl] = Field(default_factory=list)
    brand: Optional[str] = None
    title: Optional[str] = None
    category_hint: Optional[GarmentCategory] = None
    color: Optional[str] = None
    size_info: Dict[str, str] = Field(default_factory=dict)
    notes: Optional[str] = None


class CandidateSelectionRequest(BaseModel):
    input_id: str
    selected_candidate_id: str
    selected_source_image_id: Optional[str] = None
    title: Optional[str] = None
    brand: Optional[str] = None
    category_hint: Optional[GarmentCategory] = None
    color: Optional[str] = None
    size_info: Dict[str, str] = Field(default_factory=dict)
    selection_notes: Optional[str] = None

    def to_selection(self) -> GarmentSelection:
        return GarmentSelection(
            input_id=self.input_id,
            selected_candidate_id=self.selected_candidate_id,
            selected_source_image_id=self.selected_source_image_id,
            selection_notes=self.selection_notes,
        )


class TryOnPreviewRequest(BaseModel):
    avatar_id: str
    garment_id: str
    pose: str = "neutral"
    camera_angle: CameraAngle = CameraAngle.FRONT
    render_mode: RenderMode = RenderMode.STYLED_OVERLAY
    notes: Optional[str] = None


class AsyncTryOnPreviewRequest(TryOnPreviewRequest):
    force_remote_gpu: bool = True


class RemoteTryOnCallbackRequest(BaseModel):
    callback_token: str
    status: RemoteTryOnStatus


class SingleLookFeedbackRequest(BaseModel):
    job_id: str
    question: Optional[str] = None
    occasion: Optional[str] = None
    style_goal: Optional[str] = None
    notes: Optional[str] = None


class LookComparisonRequest(BaseModel):
    primary_job_id: str
    secondary_job_id: str
    occasion: Optional[str] = None
    style_goal: Optional[str] = None
    notes: Optional[str] = None


class PairingSuggestionRequest(BaseModel):
    avatar_id: str
    garment_id: str
    occasion: Optional[str] = None
    style_goal: Optional[str] = None
    weather_hint: Optional[str] = None
    notes: Optional[str] = None


class OutfitGenerationRequest(BaseModel):
    avatar_id: str
    anchor_garment_id: str
    occasion: Optional[str] = None
    style_goal: Optional[str] = None
    weather_hint: Optional[str] = None
    render_mode: RenderMode = RenderMode.STYLED_OVERLAY
    camera_angle: CameraAngle = CameraAngle.FRONT
    notes: Optional[str] = None
