from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from mira_stylist.api.dependencies import StylistServiceContainer, get_services
from mira_stylist.models import GarmentItem

router = APIRouter(prefix="/garments", tags=["garments"])


@router.get("/{garment_id}", response_model=GarmentItem)
def get_garment(
    garment_id: str,
    services: StylistServiceContainer = Depends(get_services),
) -> GarmentItem:
    garment = services.garments.get_garment(garment_id)
    if not garment:
        raise HTTPException(status_code=404, detail="Garment not found.")
    return garment
