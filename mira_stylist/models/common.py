from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel


class SourceType(str, Enum):
    LIDAR = "lidar"
    DEPTH = "depth"
    IMAGE_ESTIMATED = "image-estimated"


class CameraAngle(str, Enum):
    FRONT = "front"
    THREE_QUARTER = "three_quarter"
    SIDE = "side"
    BACK = "back"


class RenderMode(str, Enum):
    STYLED_OVERLAY = "styled_overlay"
    SILHOUETTE_FIT = "silhouette_fit"
    WIREFRAME = "wireframe"
    AR_PREVIEW = "ar_preview"


class PreviewStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def model_dump_compat(model: BaseModel) -> dict[str, Any]:
    """Support Pydantic v1 and v2 without forcing a repo-wide dependency change."""

    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")  # type: ignore[call-arg]
    return model.dict()  # type: ignore[no-any-return]
