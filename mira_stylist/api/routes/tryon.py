from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from mira_stylist.api.dependencies import StylistServiceContainer, get_services
from mira_stylist.models import (
    AsyncTryOnPreviewRequest,
    LookComparisonFeedback,
    LookComparisonRequest,
    PairingSuggestion,
    PairingSuggestionRequest,
    PreviewRenderJob,
    RemoteTryOnCallbackRequest,
    SingleLookFeedback,
    SingleLookFeedbackRequest,
    StylistTryOnJob,
    TryOnPreviewRequest,
)

router = APIRouter(prefix="/tryon", tags=["tryon"])


@router.post("/preview", response_model=PreviewRenderJob)
def create_preview(
    request: TryOnPreviewRequest,
    services: StylistServiceContainer = Depends(get_services),
) -> PreviewRenderJob:
    avatar = services.avatars.get_avatar(request.avatar_id)
    if not avatar:
        raise HTTPException(status_code=404, detail="Avatar not found.")
    avatar = services.avatars.ensure_vision_assets(avatar)
    if avatar.status.value != "ready":
        raise HTTPException(status_code=409, detail="Avatar is not ready for preview generation.")

    garment = services.garments.get_garment(request.garment_id)
    if not garment:
        raise HTTPException(status_code=404, detail="Garment not found.")
    if garment.extraction_status.value == "failed":
        raise HTTPException(status_code=409, detail="Garment is not in a previewable state.")

    return services.tryon.create_preview_job(request, avatar=avatar, garment=garment)


@router.post("/preview/async", response_model=StylistTryOnJob)
def create_preview_async(
    request: AsyncTryOnPreviewRequest,
    services: StylistServiceContainer = Depends(get_services),
) -> StylistTryOnJob:
    avatar = services.avatars.get_avatar(request.avatar_id)
    if not avatar:
        raise HTTPException(status_code=404, detail="Avatar not found.")
    avatar = services.avatars.ensure_vision_assets(avatar)
    if avatar.status.value != "ready":
        raise HTTPException(status_code=409, detail="Avatar is not ready for preview generation.")

    garment = services.garments.get_garment(request.garment_id)
    if not garment:
        raise HTTPException(status_code=404, detail="Garment not found.")
    if garment.extraction_status.value == "failed":
        raise HTTPException(status_code=409, detail="Garment is not in a previewable state.")

    return services.tryon.create_preview_job_async(request, avatar=avatar, garment=garment)


@router.get("/jobs/{job_id}", response_model=PreviewRenderJob)
def get_preview_job(
    job_id: str,
    services: StylistServiceContainer = Depends(get_services),
) -> PreviewRenderJob:
    job = services.tryon.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Preview job not found.")
    return job


@router.get("/pipeline-jobs/{stylist_job_id}", response_model=StylistTryOnJob)
def get_pipeline_job(
    stylist_job_id: str,
    services: StylistServiceContainer = Depends(get_services),
) -> StylistTryOnJob:
    job = services.stylist_jobs.get_job(stylist_job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Pipeline job not found.")
    return job


@router.post("/pipeline-jobs/{stylist_job_id}/poll", response_model=StylistTryOnJob)
def poll_pipeline_job(
    stylist_job_id: str,
    services: StylistServiceContainer = Depends(get_services),
) -> StylistTryOnJob:
    job = services.tryon.poll_pipeline_job(stylist_job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Pipeline job not found.")
    return job


@router.post("/pipeline-jobs/{stylist_job_id}/callback", response_model=StylistTryOnJob)
def apply_pipeline_callback(
    stylist_job_id: str,
    request: RemoteTryOnCallbackRequest,
    services: StylistServiceContainer = Depends(get_services),
) -> StylistTryOnJob:
    job = services.tryon.apply_pipeline_callback(
        stylist_job_id=stylist_job_id,
        callback_token=request.callback_token,
        status=request.status,
    )
    if not job:
        raise HTTPException(status_code=404, detail="Pipeline job not found or callback token invalid.")
    return job


@router.post("/feedback", response_model=SingleLookFeedback)
def single_look_feedback(
    request: SingleLookFeedbackRequest,
    services: StylistServiceContainer = Depends(get_services),
) -> SingleLookFeedback:
    job = services.tryon.get_job(request.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Preview job not found.")
    avatar = services.avatars.get_avatar(job.request.avatar_id)
    if not avatar:
        raise HTTPException(status_code=404, detail="Avatar not found for this preview job.")
    garment = services.garments.get_garment(job.request.garment_id)
    if not garment:
        raise HTTPException(status_code=404, detail="Garment not found for this preview job.")
    try:
        return services.tryon.build_single_look_feedback(
            job_id=request.job_id,
            avatar=avatar,
            garment=garment,
            question=request.question,
            occasion=request.occasion,
            style_goal=request.style_goal,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/compare", response_model=LookComparisonFeedback)
def compare_looks(
    request: LookComparisonRequest,
    services: StylistServiceContainer = Depends(get_services),
) -> LookComparisonFeedback:
    primary_job = services.tryon.get_job(request.primary_job_id)
    secondary_job = services.tryon.get_job(request.secondary_job_id)
    if not primary_job or not secondary_job:
        raise HTTPException(status_code=404, detail="One or both preview jobs were not found.")
    primary_avatar = services.avatars.get_avatar(primary_job.request.avatar_id)
    secondary_avatar = services.avatars.get_avatar(secondary_job.request.avatar_id)
    primary_garment = services.garments.get_garment(primary_job.request.garment_id)
    secondary_garment = services.garments.get_garment(secondary_job.request.garment_id)
    if not primary_avatar or not secondary_avatar:
        raise HTTPException(status_code=404, detail="Avatar not found for one or both preview jobs.")
    if not primary_garment or not secondary_garment:
        raise HTTPException(status_code=404, detail="Garment not found for one or both preview jobs.")
    try:
        return services.tryon.compare_looks(
            primary_job_id=request.primary_job_id,
            secondary_job_id=request.secondary_job_id,
            primary_avatar=primary_avatar,
            secondary_avatar=secondary_avatar,
            primary_garment=primary_garment,
            secondary_garment=secondary_garment,
            occasion=request.occasion,
            style_goal=request.style_goal,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/pairing", response_model=PairingSuggestion)
def suggest_pairings(
    request: PairingSuggestionRequest,
    services: StylistServiceContainer = Depends(get_services),
) -> PairingSuggestion:
    avatar = services.avatars.get_avatar(request.avatar_id)
    if not avatar:
        raise HTTPException(status_code=404, detail="Avatar not found.")
    garment = services.garments.get_garment(request.garment_id)
    if not garment:
        raise HTTPException(status_code=404, detail="Garment not found.")
    return services.tryon.suggest_pairings(
        avatar=avatar,
        garment=garment,
        occasion=request.occasion,
        style_goal=request.style_goal,
        weather_hint=request.weather_hint,
    )
