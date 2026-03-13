from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from mira_stylist.models import (
    RemoteTryOnAccepted,
    RemoteTryOnRequest,
    RemoteTryOnStatus,
    StylistTryOnJob,
    TryOnArtifactManifest,
    VTONInputPayload,
    VTONRunResult,
)


class VTONProvider(ABC):
    """Stable provider contract for local or remote try-on engines."""

    provider_name: str

    @abstractmethod
    def build_remote_request(
        self,
        *,
        stylist_job: StylistTryOnJob,
        artifact_manifest: TryOnArtifactManifest,
        payload: VTONInputPayload | None,
    ) -> RemoteTryOnRequest:
        raise NotImplementedError

    @abstractmethod
    def submit(
        self,
        *,
        request: RemoteTryOnRequest,
        output_dir: str | Path,
    ) -> RemoteTryOnAccepted | VTONRunResult:
        raise NotImplementedError

    @abstractmethod
    def poll(self, *, provider_job_id: str, output_dir: str | Path) -> RemoteTryOnStatus:
        raise NotImplementedError
