from __future__ import annotations

import base64
import binascii
import mimetypes
from pathlib import Path

from mira_stylist.models import CreateScanSessionRequest, ScanBetaCaptureBundleRequest, ScanCaptureBundle, ScanSession
from mira_stylist.models.avatar import AvatarStatus
from mira_stylist.models.common import utc_now
from mira_stylist.utils import sanitize_filename, sha256_bytes
from mira_stylist.utils.ids import new_prefixed_id

from .storage_service import AssetStorageService


class ScanSessionService:
    """Manage mobile scan sessions before avatar reconstruction is triggered."""

    def __init__(self, storage: AssetStorageService):
        self.storage = storage
        self._sessions: dict[str, ScanSession] = {}
        self._bundles: dict[str, ScanCaptureBundle] = {}
        self._load_existing_sessions()
        self._load_existing_bundles()

    def create_scan_session(self, request: CreateScanSessionRequest) -> ScanSession:
        """
        Create a scan session record.

        TODO:
        - accept uploaded frame bundles or cloud object references
        - evaluate scan coverage, missing angles, and depth consistency
        - track on-device AR session metadata and calibration drift
        """

        scan_session_id = new_prefixed_id("scan")
        session = ScanSession(
            scan_session_id=scan_session_id,
            user_id=request.user_id,
            source_type=request.source_type,
            capture_device_model=request.capture_device_model,
            has_lidar=request.has_lidar,
            frame_count=request.frame_count,
            depth_frame_count=request.depth_frame_count,
            image_resolution=request.image_resolution,
            quality_score=self._estimate_quality(request.frame_count, request.depth_frame_count),
            status=AvatarStatus.SCAN_PENDING,
            notes=request.notes,
        )
        paths = self.storage.ensure_scan_session_paths(request.user_id, scan_session_id)
        self.storage.write_metadata(paths.metadata_dir / "scan_session.json", session)
        self._sessions[scan_session_id] = session
        return session

    def get_scan_session(self, scan_session_id: str) -> ScanSession | None:
        session = self._sessions.get(scan_session_id)
        if session:
            return session
        return self._load_session_from_disk(scan_session_id)

    def mark_processing(self, scan_session_id: str) -> ScanSession | None:
        session = self.get_scan_session(scan_session_id)
        if not session:
            return None
        session.status = AvatarStatus.PROCESSING
        session.updated_at = utc_now()
        self._persist_session(session)
        return session

    def mark_ready(self, scan_session_id: str) -> ScanSession | None:
        session = self.get_scan_session(scan_session_id)
        if not session:
            return None
        session.status = AvatarStatus.READY
        session.updated_at = utc_now()
        self._persist_session(session)
        return session

    def mark_failed(self, scan_session_id: str, reason: str) -> ScanSession | None:
        session = self.get_scan_session(scan_session_id)
        if not session:
            return None
        session.status = AvatarStatus.FAILED
        session.notes = reason
        session.updated_at = utc_now()
        self._persist_session(session)
        return session

    def register_capture_bundle(
        self,
        scan_session_id: str,
        request: ScanBetaCaptureBundleRequest,
    ) -> ScanCaptureBundle:
        session = self.get_scan_session(scan_session_id)
        if not session:
            raise ValueError("Scan session not found.")

        bundle_id = new_prefixed_id("bundle")
        paths = self.storage.ensure_scan_session_paths(session.user_id, scan_session_id)
        preview_path = None
        if request.preview_image_base64:
            preview_bytes = self._decode_base64_payload(request.preview_image_base64)
            preview_name = sanitize_filename(request.preview_original_filename, fallback_stem="scan_preview")
            suffix = Path(preview_name).suffix or mimetypes.guess_extension(request.preview_mime_type or "") or ".bin"
            preview_path = paths.uploads_dir / f"{Path(preview_name).stem}_{bundle_id}{suffix}"
            self.storage.write_binary(preview_path, preview_bytes)
            self.storage.write_metadata(
                paths.metadata_dir / f"preview_{bundle_id}.json",
                {
                    "path": str(preview_path),
                    "mime_type": request.preview_mime_type,
                    "sha256": sha256_bytes(preview_bytes),
                },
            )

        bundle = ScanCaptureBundle(
            bundle_id=bundle_id,
            scan_session_id=scan_session_id,
            upload_mode=request.upload_mode,
            rgb_frame_count=request.rgb_frame_count,
            depth_frame_count=request.depth_frame_count,
            lidar_point_count=request.lidar_point_count,
            image_resolution=request.image_resolution,
            depth_resolution=request.depth_resolution,
            duration_ms=request.duration_ms,
            coverage_score=self._bundle_coverage(request),
            preview_image_path=str(preview_path) if preview_path else None,
            bundle_reference=request.bundle_reference,
            notes=request.notes,
        )
        self.storage.write_metadata(paths.metadata_dir / f"capture_bundle_{bundle_id}.json", bundle)
        self.storage.write_metadata(paths.metadata_dir / "latest_capture_bundle.json", bundle)
        self._bundles[scan_session_id] = bundle

        session.frame_count = max(session.frame_count, request.rgb_frame_count)
        session.depth_frame_count = max(session.depth_frame_count, request.depth_frame_count)
        session.image_resolution = request.image_resolution or session.image_resolution
        session.quality_score = max(session.quality_score, bundle.coverage_score, self._estimate_quality(session.frame_count, session.depth_frame_count))
        session.updated_at = utc_now()
        self._persist_session(session)
        return bundle

    def get_capture_bundle(self, scan_session_id: str) -> ScanCaptureBundle | None:
        bundle = self._bundles.get(scan_session_id)
        if bundle:
            return bundle
        matches = self.storage.glob(f"scan_sessions/*/{scan_session_id}/metadata/latest_capture_bundle.json")
        if not matches:
            return None
        bundle = self.storage.read_model(matches[0], ScanCaptureBundle)
        if bundle:
            self._bundles[scan_session_id] = bundle
        return bundle

    @staticmethod
    def _estimate_quality(frame_count: int, depth_frame_count: int) -> float:
        if frame_count <= 0:
            return 0.0
        base = min(frame_count / 120.0, 1.0)
        depth_bonus = min(depth_frame_count / 80.0, 1.0) * 0.2
        return round(min(base * 0.8 + depth_bonus, 1.0), 2)

    def _persist_session(self, session: ScanSession) -> None:
        paths = self.storage.ensure_scan_session_paths(session.user_id, session.scan_session_id)
        self.storage.write_metadata(paths.metadata_dir / "scan_session.json", session)
        self._sessions[session.scan_session_id] = session

    def _load_existing_sessions(self) -> None:
        for path in self.storage.glob("scan_sessions/*/*/metadata/scan_session.json"):
            session = self.storage.read_model(path, ScanSession)
            if session:
                self._sessions[session.scan_session_id] = session

    def _load_existing_bundles(self) -> None:
        for path in self.storage.glob("scan_sessions/*/*/metadata/latest_capture_bundle.json"):
            bundle = self.storage.read_model(path, ScanCaptureBundle)
            if bundle:
                self._bundles[bundle.scan_session_id] = bundle

    def _load_session_from_disk(self, scan_session_id: str) -> ScanSession | None:
        matches = self.storage.glob(f"scan_sessions/*/{scan_session_id}/metadata/scan_session.json")
        if not matches:
            return None
        session = self.storage.read_model(matches[0], ScanSession)
        if session:
            self._sessions[scan_session_id] = session
        return session

    @staticmethod
    def _bundle_coverage(request: ScanBetaCaptureBundleRequest) -> float:
        coverage = min(request.rgb_frame_count / 150.0, 1.0) * 0.45
        coverage += min(request.depth_frame_count / 90.0, 1.0) * 0.35
        if request.lidar_point_count:
            coverage += min(request.lidar_point_count / 50000.0, 1.0) * 0.1
        coverage += min(max(request.coverage_hint, 0.0), 1.0) * 0.1
        return round(min(coverage, 1.0), 2)

    @staticmethod
    def _decode_base64_payload(payload: str) -> bytes:
        try:
            if "," in payload and payload.strip().startswith("data:"):
                payload = payload.split(",", 1)[1]
            return base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Invalid base64 image payload.") from exc
