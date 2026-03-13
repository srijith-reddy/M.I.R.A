from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from mira_stylist.config import StylistSettings, get_settings

from .artifact_url_service import ArtifactURLService


class ObjectStoreService:
    """Publish artifact read URLs through either backend-signed URLs or an external object-store base URL."""

    def __init__(
        self,
        settings: StylistSettings | None = None,
        artifact_urls: ArtifactURLService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.artifact_urls = artifact_urls or ArtifactURLService(settings=self.settings)

    def publish_artifact(self, path: str | Path) -> str | None:
        mode = self.settings.object_store_mode
        if mode == "external_base" and self.settings.object_store_base_url:
            relative = self._relative_storage_path(path)
            return f"{self.settings.object_store_base_url.rstrip('/')}/{quote(relative)}"
        return self.artifact_urls.build_signed_url(path)

    def _relative_storage_path(self, path: str | Path) -> str:
        raw = Path(path).resolve()
        try:
            return raw.relative_to(self.settings.storage_root.resolve()).as_posix()
        except ValueError:
            return raw.as_posix().lstrip("/")
