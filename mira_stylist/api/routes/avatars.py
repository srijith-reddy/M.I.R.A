from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from mira_stylist.api.dependencies import StylistServiceContainer, get_services
from mira_stylist.models import (
    AvatarPhotoCaptureRequest,
    CreateAvatarRequest,
    CreateScanSessionRequest,
    QuickTryOnAvatarRequest,
    ScanBetaBuildRequest,
    ScanBetaCaptureBundleRequest,
    ScanBetaSessionRequest,
    ScanCaptureBundle,
    ScanSession,
    UserAvatar,
)

router = APIRouter(prefix="/avatars", tags=["avatars"])


@router.post("/scan-session", response_model=ScanSession)
def create_scan_session(
    request: CreateScanSessionRequest,
    services: StylistServiceContainer = Depends(get_services),
) -> ScanSession:
    return services.scan_sessions.create_scan_session(request)


@router.post("", response_model=UserAvatar)
def create_avatar(
    request: CreateAvatarRequest,
    services: StylistServiceContainer = Depends(get_services),
) -> UserAvatar:
    if request.scan_session_id and not services.scan_sessions.get_scan_session(request.scan_session_id):
        raise HTTPException(status_code=404, detail="Scan session not found.")
    return services.avatars.create_avatar(request)


@router.post("/photo-profile", response_model=UserAvatar)
def create_avatar_from_photos(
    request: AvatarPhotoCaptureRequest,
    services: StylistServiceContainer = Depends(get_services),
) -> UserAvatar:
    try:
        return services.avatars.create_avatar_from_photos(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/quick-tryon", response_model=UserAvatar)
def create_avatar_for_quick_tryon(
    request: QuickTryOnAvatarRequest,
    services: StylistServiceContainer = Depends(get_services),
) -> UserAvatar:
    try:
        return services.avatars.create_avatar_from_quick_photo(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/scan-beta/session", response_model=ScanSession)
def create_scan_beta_session(
    request: ScanBetaSessionRequest,
    services: StylistServiceContainer = Depends(get_services),
) -> ScanSession:
    return services.scan_sessions.create_scan_session(
        CreateScanSessionRequest(
            user_id=request.user_id,
            source_type=request.source_type,
            capture_device_model=request.capture_device_model,
            has_lidar=request.has_lidar,
            frame_count=request.expected_frame_count,
            depth_frame_count=request.expected_depth_frame_count,
            image_resolution=request.image_resolution,
            notes=request.notes,
        )
    )


@router.post("/scan-beta/session/{scan_session_id}/capture-bundle", response_model=ScanCaptureBundle)
def register_scan_beta_capture_bundle(
    scan_session_id: str,
    request: ScanBetaCaptureBundleRequest,
    services: StylistServiceContainer = Depends(get_services),
) -> ScanCaptureBundle:
    try:
        return services.scan_sessions.register_capture_bundle(scan_session_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/scan-beta/build", response_model=UserAvatar)
def build_avatar_from_scan_beta(
    request: ScanBetaBuildRequest,
    services: StylistServiceContainer = Depends(get_services),
) -> UserAvatar:
    try:
        return services.avatars.create_avatar_from_scan_beta(request)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "not found" in detail.lower() else 409
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.get("/{avatar_id}", response_model=UserAvatar)
def get_avatar(
    avatar_id: str,
    services: StylistServiceContainer = Depends(get_services),
) -> UserAvatar:
    avatar = services.avatars.get_avatar(avatar_id)
    if not avatar:
        raise HTTPException(status_code=404, detail="Avatar not found.")
    return avatar
