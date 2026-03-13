from .avatars import router as avatars_router
from .garment_ingestion import router as garment_ingestion_router
from .garments import router as garments_router
from .health import router as health_router
from .outfits import router as outfits_router
from .tryon import router as tryon_router

__all__ = [
    "avatars_router",
    "garment_ingestion_router",
    "garments_router",
    "health_router",
    "outfits_router",
    "tryon_router",
]
