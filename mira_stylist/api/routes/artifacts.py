from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from mira_stylist.api.dependencies import StylistServiceContainer, get_services

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("/{artifact_path:path}", include_in_schema=False)
def get_signed_artifact(
    artifact_path: str,
    expires: int = Query(...),
    sig: str = Query(...),
    services: StylistServiceContainer = Depends(get_services),
) -> FileResponse:
    if not services.artifact_urls.verify(artifact_path, expires=expires, signature=sig):
        raise HTTPException(status_code=403, detail="Invalid artifact signature.")
    target = services.artifact_urls.resolve_signed_path(artifact_path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return FileResponse(target)
