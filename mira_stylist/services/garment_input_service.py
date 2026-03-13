from __future__ import annotations

import base64
import binascii
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

from mira_stylist.models.garment_input import GarmentInput, GarmentInputStatus, GarmentInputType
from mira_stylist.models.common import utc_now
from mira_stylist.utils import inspect_image_bytes, sanitize_filename, sha256_bytes
from mira_stylist.utils.ids import new_prefixed_id

from .storage_service import AssetStorageService


class GarmentInputService:
    """Accept and persist raw image-first inputs before normalization."""

    def __init__(self, storage: AssetStorageService):
        self.storage = storage
        self._inputs: dict[str, GarmentInput] = {}
        self._load_existing_inputs()

    def create_uploaded_image(
        self,
        *,
        uploaded_by: str,
        image_bytes: bytes,
        original_filename: str | None,
        mime_type: str | None,
        notes: str | None = None,
        referring_page_url: str | None = None,
    ) -> GarmentInput:
        return self._create_binary_input(
            input_type=GarmentInputType.UPLOADED_IMAGE,
            uploaded_by=uploaded_by,
            image_bytes=image_bytes,
            original_filename=original_filename,
            mime_type=mime_type,
            notes=notes,
            referring_page_url=referring_page_url,
        )

    def create_pasted_image(
        self,
        *,
        uploaded_by: str,
        image_base64: str,
        original_filename: str | None,
        mime_type: str | None,
        notes: str | None = None,
        referring_page_url: str | None = None,
    ) -> GarmentInput:
        image_bytes = self._decode_base64_payload(image_base64)
        return self._create_binary_input(
            input_type=GarmentInputType.PASTED_IMAGE,
            uploaded_by=uploaded_by,
            image_bytes=image_bytes,
            original_filename=original_filename,
            mime_type=mime_type,
            notes=notes,
            referring_page_url=referring_page_url,
        )

    def create_screenshot(
        self,
        *,
        uploaded_by: str,
        image_base64: str,
        original_filename: str | None,
        mime_type: str | None,
        notes: str | None = None,
        referring_page_url: str | None = None,
    ) -> GarmentInput:
        image_bytes = self._decode_base64_payload(image_base64)
        return self._create_binary_input(
            input_type=GarmentInputType.SCREENSHOT,
            uploaded_by=uploaded_by,
            image_bytes=image_bytes,
            original_filename=original_filename,
            mime_type=mime_type,
            notes=notes,
            referring_page_url=referring_page_url,
        )

    def create_image_url(
        self,
        *,
        uploaded_by: str,
        image_url: str,
        notes: str | None = None,
        referring_page_url: str | None = None,
    ) -> GarmentInput:
        input_id = new_prefixed_id("ginput")
        paths = self.storage.ensure_garment_input_paths(input_id)
        self.storage.write_text(paths.raw_dir / "image_url.txt", image_url)
        original_filename = sanitize_filename(Path(urlparse(image_url).path).name or None, fallback_stem="remote_image")
        garment_input = GarmentInput(
            input_id=input_id,
            input_type=GarmentInputType.IMAGE_URL,
            original_filename=original_filename,
            source_url=image_url,
            referring_page_url=referring_page_url,
            mime_type=mimetypes.guess_type(image_url)[0],
            uploaded_by=uploaded_by,
            raw_asset_path=str(paths.raw_dir / "image_url.txt"),
            normalized_asset_path=str(paths.normalized_dir),
            notes=notes,
            status=GarmentInputStatus.RECEIVED,
        )
        self._persist_input(garment_input)
        return garment_input

    def create_product_page_url(
        self,
        *,
        uploaded_by: str,
        product_page_url: str,
        image_urls: list[str] | None = None,
        notes: str | None = None,
    ) -> GarmentInput:
        input_id = new_prefixed_id("ginput")
        paths = self.storage.ensure_garment_input_paths(input_id)
        self.storage.write_text(paths.raw_dir / "product_page_url.txt", product_page_url)
        garment_input = GarmentInput(
            input_id=input_id,
            input_type=GarmentInputType.PRODUCT_PAGE_URL,
            source_url=product_page_url,
            supplemental_image_urls=image_urls or [],
            mime_type="text/uri-list",
            uploaded_by=uploaded_by,
            raw_asset_path=str(paths.raw_dir / "product_page_url.txt"),
            normalized_asset_path=str(paths.normalized_dir),
            notes=notes,
            status=GarmentInputStatus.RECEIVED,
        )
        self._persist_input(garment_input)
        return garment_input

    def get_input(self, input_id: str) -> GarmentInput | None:
        garment_input = self._inputs.get(input_id)
        if garment_input:
            return garment_input
        return self._load_input_from_disk(input_id)

    def update_status(self, input_id: str, status: GarmentInputStatus) -> GarmentInput | None:
        garment_input = self._inputs.get(input_id)
        if not garment_input:
            return None
        garment_input.status = status
        garment_input.updated_at = utc_now()
        self._persist_input(garment_input)
        return garment_input

    def _create_binary_input(
        self,
        *,
        input_type: GarmentInputType,
        uploaded_by: str,
        image_bytes: bytes,
        original_filename: str | None,
        mime_type: str | None,
        notes: str | None,
        referring_page_url: str | None,
    ) -> GarmentInput:
        input_id = new_prefixed_id("ginput")
        paths = self.storage.ensure_garment_input_paths(input_id)
        inferred_mime, width, height = inspect_image_bytes(image_bytes)
        resolved_mime = mime_type or inferred_mime
        suffix = Path(original_filename or "input.bin").suffix or self._suffix_from_mime(resolved_mime)
        safe_name = sanitize_filename(original_filename, fallback_stem="source")
        raw_path = paths.raw_dir / (Path(safe_name).stem + suffix)
        self.storage.write_binary(raw_path, image_bytes)
        content_sha = sha256_bytes(image_bytes)
        garment_input = GarmentInput(
            input_id=input_id,
            input_type=input_type,
            original_filename=safe_name,
            referring_page_url=referring_page_url,
            mime_type=resolved_mime,
            uploaded_by=uploaded_by,
            image_width=width,
            image_height=height,
            file_size_bytes=len(image_bytes),
            content_sha256=content_sha,
            raw_asset_path=str(raw_path),
            normalized_asset_path=str(paths.normalized_dir),
            notes=notes,
            status=GarmentInputStatus.RECEIVED,
        )
        self._persist_input(garment_input)
        return garment_input

    def _persist_input(self, garment_input: GarmentInput) -> None:
        paths = self.storage.ensure_garment_input_paths(garment_input.input_id)
        self.storage.write_metadata(paths.metadata_dir / "garment_input.json", garment_input)
        self._inputs[garment_input.input_id] = garment_input

    def _load_existing_inputs(self) -> None:
        for path in self.storage.glob("garment_inputs/*/metadata/garment_input.json"):
            garment_input = self.storage.read_model(path, GarmentInput)
            if garment_input:
                self._inputs[garment_input.input_id] = garment_input

    def _load_input_from_disk(self, input_id: str) -> GarmentInput | None:
        matches = self.storage.glob(f"garment_inputs/{input_id}/metadata/garment_input.json")
        if not matches:
            return None
        garment_input = self.storage.read_model(matches[0], GarmentInput)
        if garment_input:
            self._inputs[input_id] = garment_input
        return garment_input

    @staticmethod
    def _suffix_from_mime(mime_type: str | None) -> str:
        guessed = mimetypes.guess_extension(mime_type or "") or ".bin"
        return guessed

    @staticmethod
    def _decode_base64_payload(payload: str) -> bytes:
        try:
            if "," in payload and payload.strip().startswith("data:"):
                payload = payload.split(",", 1)[1]
            return base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Invalid base64 image payload.") from exc
