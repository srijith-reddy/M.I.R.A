from __future__ import annotations

import uuid


def new_prefixed_id(prefix: str) -> str:
    """Generate stable human-readable IDs for storage and API responses."""

    return f"{prefix}_{uuid.uuid4().hex[:12]}"
