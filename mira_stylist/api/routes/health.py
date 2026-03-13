from __future__ import annotations

from fastapi import APIRouter, Depends

from mira_stylist.api.dependencies import StylistServiceContainer, get_services
from mira_stylist.models import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(services: StylistServiceContainer = Depends(get_services)) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="mira_stylist",
        version=services.settings.api_version,
    )
