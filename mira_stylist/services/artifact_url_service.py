from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
import time
from urllib.parse import quote

from mira_stylist.config import StylistSettings, get_settings


class ArtifactURLService:
    """Create and verify signed artifact URLs for remote worker access."""

    def __init__(self, settings: StylistSettings | None = None):
        self.settings = settings or get_settings()

    def build_signed_url(self, path: str | Path, *, expires: int | None = None) -> str | None:
        base_url = (self.settings.artifact_base_url or "").rstrip("/")
        if not base_url:
            return None
        relative = self._relative_storage_path(path)
        expiry = expires or int(time.time()) + self.settings.artifact_url_ttl_seconds
        sig = self._signature(relative, expiry)
        return f"{base_url}/{quote(relative)}?expires={expiry}&sig={sig}"

    def verify(self, relative_path: str, *, expires: int, signature: str) -> bool:
        if expires < int(time.time()):
            return False
        return hmac.compare_digest(self._signature(relative_path, expires), signature)

    def resolve_signed_path(self, relative_path: str) -> Path:
        candidate = (self.settings.storage_root / relative_path).resolve()
        return candidate

    def _relative_storage_path(self, path: str | Path) -> str:
        raw = Path(path).resolve()
        try:
            return raw.relative_to(self.settings.storage_root).as_posix()
        except ValueError:
            return raw.name

    def _signature(self, relative_path: str, expires: int) -> str:
        message = f"{relative_path}:{expires}".encode("utf-8")
        secret = self.settings.artifact_signing_secret.encode("utf-8")
        return hmac.new(secret, message, hashlib.sha256).hexdigest()
