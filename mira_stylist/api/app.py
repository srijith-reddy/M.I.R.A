from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from mira_stylist.api.routes.avatars import router as avatars_router
from mira_stylist.api.routes.artifacts import router as artifacts_router
from mira_stylist.api.routes.garment_ingestion import router as garment_ingestion_router
from mira_stylist.api.routes.garments import router as garments_router
from mira_stylist.api.routes.health import router as health_router
from mira_stylist.api.routes.outfits import router as outfits_router
from mira_stylist.api.routes.tryon import router as tryon_router
from mira_stylist.config import get_settings

DEMO_DIR = Path(__file__).resolve().parent.parent / "demo"


def create_app() -> FastAPI:
    """Create a standalone FastAPI app for MIRA Stylist."""

    settings = get_settings()
    app = FastAPI(
        title=settings.api_title,
        version=settings.api_version,
        description="Scaffold API for MIRA Stylist avatar, garment, and preview workflows.",
    )

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/demo")

    @app.get("/demo", response_class=HTMLResponse, include_in_schema=False)
    def demo() -> HTMLResponse:
        return HTMLResponse((DEMO_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/demo-artifacts/{artifact_path:path}", include_in_schema=False)
    def demo_artifact(artifact_path: str) -> FileResponse:
        target = settings.storage_root / artifact_path
        if not target.exists():
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Artifact not found.")
        return FileResponse(target)

    app.include_router(health_router)
    app.include_router(artifacts_router)
    app.include_router(avatars_router)
    app.include_router(garment_ingestion_router)
    app.include_router(garments_router)
    app.include_router(outfits_router)
    app.include_router(tryon_router)
    return app


app = create_app()
