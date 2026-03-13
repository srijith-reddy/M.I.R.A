from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from mira_stylist.api.dependencies import StylistServiceContainer, get_services
from mira_stylist.models import (
    CandidateSelectionRequest,
    GarmentItem,
    GarmentIngestionRequest,
    GarmentIngestionResult,
    ImageUrlIngestRequest,
    PastedImageIngestRequest,
    ProductPageUrlIngestRequest,
    ScreenshotIngestRequest,
)

router = APIRouter(prefix="/garments/ingest", tags=["garment_ingestion"])


def _build_request(
    *,
    input_id: str,
    uploaded_by: str,
    title: str | None,
    brand: str | None,
    category_hint: str | None,
    color: str | None,
    size_info: dict[str, str],
    notes: str | None,
) -> GarmentIngestionRequest:
    return GarmentIngestionRequest(
        input_id=input_id,
        uploaded_by=uploaded_by,
        title=title,
        brand=brand,
        category_hint=category_hint,
        color=color,
        size_info=size_info,
        notes=notes,
    )


def _auto_finalize_if_unambiguous(
    result: GarmentIngestionResult,
    request: GarmentIngestionRequest,
    services: StylistServiceContainer,
) -> GarmentIngestionResult:
    if len(result.candidate_images) != 1:
        return result
    candidate = result.candidate_images[0]
    if candidate.requires_user_confirmation:
        return result
    garment = services.candidate_selection.select_candidate(
        selection=CandidateSelectionRequest(
            input_id=result.input_id,
            selected_candidate_id=candidate.candidate_id,
            selected_source_image_id=candidate.source_image_id,
            title=request.title,
            brand=request.brand,
            category_hint=None,
            color=request.color,
            size_info=request.size_info,
            selection_notes="Auto-selected because the ingestion produced a single high-confidence candidate.",
        ).to_selection(),
        title=request.title,
        brand=request.brand,
        category_hint=services.garment_ingestion.get_request_category(result.input_id),
        color=request.color,
        size_info=request.size_info,
    )
    updated = services.garment_ingestion.mark_selected(
        input_id=result.input_id,
        garment_id=garment.garment_id,
        selected_candidate_id=candidate.candidate_id,
    )
    return updated or result


@router.post("/image-upload", response_model=GarmentIngestionResult)
async def ingest_image_upload(
    uploaded_by: str = Form(...),
    file: UploadFile = File(...),
    notes: str | None = Form(default=None),
    referring_page_url: str | None = Form(default=None),
    title: str | None = Form(default=None),
    brand: str | None = Form(default=None),
    category_hint: str | None = Form(default=None),
    color: str | None = Form(default=None),
    services: StylistServiceContainer = Depends(get_services),
) -> GarmentIngestionResult:
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    garment_input = services.garment_inputs.create_uploaded_image(
        uploaded_by=uploaded_by,
        image_bytes=image_bytes,
        original_filename=file.filename,
        mime_type=file.content_type,
        notes=notes,
        referring_page_url=referring_page_url,
    )
    request = _build_request(
        input_id=garment_input.input_id,
        uploaded_by=uploaded_by,
        title=title,
        brand=brand,
        category_hint=category_hint,
        color=color,
        size_info={},
        notes=notes,
    )
    result = services.garment_ingestion.ingest_input(garment_input, request)
    return _auto_finalize_if_unambiguous(result, request, services)


@router.post("/image-url", response_model=GarmentIngestionResult)
def ingest_image_url(
    request: ImageUrlIngestRequest,
    services: StylistServiceContainer = Depends(get_services),
) -> GarmentIngestionResult:
    garment_input = services.garment_inputs.create_image_url(
        uploaded_by=request.uploaded_by,
        image_url=str(request.image_url),
        notes=request.notes,
        referring_page_url=str(request.referring_page_url) if request.referring_page_url else None,
    )
    ingestion_request = _build_request(
        input_id=garment_input.input_id,
        uploaded_by=request.uploaded_by,
        title=request.title,
        brand=request.brand,
        category_hint=request.category_hint.value if request.category_hint else None,
        color=request.color,
        size_info=request.size_info,
        notes=request.notes,
    )
    result = services.garment_ingestion.ingest_input(garment_input, ingestion_request)
    return _auto_finalize_if_unambiguous(result, ingestion_request, services)


@router.post("/pasted-image", response_model=GarmentIngestionResult)
def ingest_pasted_image(
    request: PastedImageIngestRequest,
    services: StylistServiceContainer = Depends(get_services),
) -> GarmentIngestionResult:
    try:
        garment_input = services.garment_inputs.create_pasted_image(
            uploaded_by=request.uploaded_by,
            image_base64=request.image_base64,
            original_filename=request.original_filename,
            mime_type=request.mime_type,
            notes=request.notes,
            referring_page_url=str(request.referring_page_url) if request.referring_page_url else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ingestion_request = _build_request(
        input_id=garment_input.input_id,
        uploaded_by=request.uploaded_by,
        title=request.title,
        brand=request.brand,
        category_hint=request.category_hint.value if request.category_hint else None,
        color=request.color,
        size_info=request.size_info,
        notes=request.notes,
    )
    result = services.garment_ingestion.ingest_input(garment_input, ingestion_request)
    return _auto_finalize_if_unambiguous(result, ingestion_request, services)


@router.post("/screenshot", response_model=GarmentIngestionResult)
def ingest_screenshot(
    request: ScreenshotIngestRequest,
    services: StylistServiceContainer = Depends(get_services),
) -> GarmentIngestionResult:
    try:
        garment_input = services.garment_inputs.create_screenshot(
            uploaded_by=request.uploaded_by,
            image_base64=request.image_base64,
            original_filename=request.original_filename,
            mime_type=request.mime_type,
            notes=request.notes,
            referring_page_url=str(request.referring_page_url) if request.referring_page_url else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ingestion_request = _build_request(
        input_id=garment_input.input_id,
        uploaded_by=request.uploaded_by,
        title=request.title,
        brand=request.brand,
        category_hint=request.category_hint.value if request.category_hint else None,
        color=request.color,
        size_info=request.size_info,
        notes=request.notes,
    )
    result = services.garment_ingestion.ingest_input(garment_input, ingestion_request)
    return _auto_finalize_if_unambiguous(result, ingestion_request, services)


@router.post("/product-page-url", response_model=GarmentIngestionResult)
def ingest_product_page_url(
    request: ProductPageUrlIngestRequest,
    services: StylistServiceContainer = Depends(get_services),
) -> GarmentIngestionResult:
    garment_input = services.garment_inputs.create_product_page_url(
        uploaded_by=request.uploaded_by,
        product_page_url=str(request.product_page_url),
        image_urls=[str(url) for url in request.image_urls],
        notes=request.notes,
    )
    ingestion_request = _build_request(
        input_id=garment_input.input_id,
        uploaded_by=request.uploaded_by,
        title=request.title,
        brand=request.brand,
        category_hint=request.category_hint.value if request.category_hint else None,
        color=request.color,
        size_info=request.size_info,
        notes=request.notes,
    )
    result = services.garment_ingestion.ingest_input(garment_input, ingestion_request)
    return _auto_finalize_if_unambiguous(result, ingestion_request, services)


@router.post("/select-candidate", response_model=GarmentItem)
def select_candidate(
    request: CandidateSelectionRequest,
    services: StylistServiceContainer = Depends(get_services),
):
    try:
        garment = services.candidate_selection.select_candidate(
            selection=request.to_selection(),
            title=request.title,
            brand=request.brand,
            category_hint=request.category_hint,
            color=request.color,
            size_info=request.size_info,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    services.garment_ingestion.mark_selected(
        input_id=request.input_id,
        garment_id=garment.garment_id,
        selected_candidate_id=request.selected_candidate_id,
    )
    return garment
