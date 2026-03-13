from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from mira_stylist.api.dependencies import StylistServiceContainer, get_services
from mira_stylist.models import GeneratedOutfit, OutfitGenerationRequest

router = APIRouter(prefix="/outfits", tags=["outfits"])


@router.post("/generate", response_model=GeneratedOutfit)
def generate_outfit(
    request: OutfitGenerationRequest,
    services: StylistServiceContainer = Depends(get_services),
) -> GeneratedOutfit:
    avatar = services.avatars.get_avatar(request.avatar_id)
    if not avatar:
        raise HTTPException(status_code=404, detail="Avatar not found.")
    garment = services.garments.get_garment(request.anchor_garment_id)
    if not garment:
        raise HTTPException(status_code=404, detail="Anchor garment not found.")
    if avatar.status.value != "ready":
        raise HTTPException(status_code=409, detail="Avatar is not ready for outfit generation.")
    if garment.extraction_status.value == "failed":
        raise HTTPException(status_code=409, detail="Anchor garment is not available for outfit generation.")
    return services.outfits.generate_outfit(request, avatar=avatar, anchor_garment=garment)


@router.get("/{outfit_id}", response_model=GeneratedOutfit)
def get_outfit(
    outfit_id: str,
    services: StylistServiceContainer = Depends(get_services),
) -> GeneratedOutfit:
    outfit = services.outfits.get_outfit(outfit_id)
    if not outfit:
        raise HTTPException(status_code=404, detail="Outfit not found.")
    return outfit
