from __future__ import annotations

import hashlib
import re
from pathlib import Path


def sanitize_filename(filename: str | None, fallback_stem: str = "asset") -> str:
    """Return a filesystem-safe filename while preserving a simple extension."""

    if not filename:
        return fallback_stem
    path = Path(filename)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._") or fallback_stem
    suffix = re.sub(r"[^A-Za-z0-9.]+", "", path.suffix)[:10]
    return f"{stem}{suffix}"


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
