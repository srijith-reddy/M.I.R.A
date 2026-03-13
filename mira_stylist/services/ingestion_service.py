from __future__ import annotations

import json

from mira_stylist.models.garment import GarmentCategory
from mira_stylist.models.garment_input import (
    GarmentIngestionRequest,
    GarmentIngestionResult,
    GarmentInput,
    GarmentInputStatus,
)
from mira_stylist.models.common import utc_now
from mira_stylist.pipelines import InputNormalizationPipeline

from .garment_input_service import GarmentInputService
from .storage_service import AssetStorageService


class GarmentIngestionService:
    """Normalize garment inputs and produce candidate-selection results."""

    def __init__(
        self,
        storage: AssetStorageService,
        inputs: GarmentInputService,
        pipeline: InputNormalizationPipeline | None = None,
    ):
        self.storage = storage
        self.inputs = inputs
        self.pipeline = pipeline or InputNormalizationPipeline()
        self._results: dict[str, GarmentIngestionResult] = {}
        self._requests: dict[str, GarmentIngestionRequest] = {}
        self._load_existing_state()

    def ingest_input(
        self, garment_input: GarmentInput, request: GarmentIngestionRequest
    ) -> GarmentIngestionResult:
        """
        Normalize an input into source images and candidate garments.

        TODO:
        - run real segmentation proposal generation
        - allow image deduplication and better confidence modeling
        - support async processing for heavier CV stages
        """

        storage_paths = self.storage.ensure_garment_input_paths(garment_input.input_id)
        artifacts = self.pipeline.normalize(garment_input, storage_paths)
        result = GarmentIngestionResult(
            input_id=garment_input.input_id,
            status=artifacts.status,
            source_metadata=artifacts.source_metadata,
            source_images=artifacts.source_images,
            candidate_images=artifacts.candidate_images,
            primary_candidate_id=artifacts.candidate_images[0].candidate_id if artifacts.candidate_images else None,
            confidence_scores=artifacts.confidence_scores,
            notes=artifacts.notes,
        )
        result.updated_at = utc_now()
        self._materialize_candidate_artifacts(result)
        self.storage.write_metadata(storage_paths.metadata_dir / "request.json", request)
        self.storage.write_metadata(storage_paths.metadata_dir / "ingestion_result.json", result)
        self.storage.write_metadata(storage_paths.metadata_dir / "source_images.json", {"source_images": result.source_images})
        self.storage.write_metadata(storage_paths.metadata_dir / "candidate_images.json", {"candidate_images": result.candidate_images})
        self.inputs.update_status(garment_input.input_id, artifacts.status)
        self._results[garment_input.input_id] = result
        self._requests[garment_input.input_id] = request
        return result

    def mark_selected(self, input_id: str, garment_id: str, selected_candidate_id: str) -> GarmentIngestionResult | None:
        result = self.get_result(input_id)
        if not result:
            return None
        result.status = GarmentInputStatus.SELECTED
        result.garment_id = garment_id
        result.selected_candidate_id = selected_candidate_id
        result.updated_at = utc_now()
        storage_paths = self.storage.ensure_garment_input_paths(input_id)
        self.storage.write_metadata(storage_paths.metadata_dir / "ingestion_result.json", result)
        self.inputs.update_status(input_id, GarmentInputStatus.SELECTED)
        return result

    def get_result(self, input_id: str) -> GarmentIngestionResult | None:
        result = self._results.get(input_id)
        if result:
            return result
        return self._load_result_from_disk(input_id)

    def get_request(self, input_id: str) -> GarmentIngestionRequest | None:
        request = self._requests.get(input_id)
        if request:
            return request
        return self._load_request_from_disk(input_id)

    def get_request_category(self, input_id: str) -> GarmentCategory | None:
        request = self.get_request(input_id)
        if not request or not request.category_hint:
            return None
        try:
            return GarmentCategory(request.category_hint)
        except ValueError:
            return None

    def _materialize_candidate_artifacts(self, result: GarmentIngestionResult) -> None:
        for candidate in result.candidate_images:
            if not candidate.local_preview_path:
                continue
            self.storage.write_text(candidate.local_preview_path, self._candidate_svg(candidate))

    def _load_existing_state(self) -> None:
        for path in self.storage.glob("garment_inputs/*/metadata/ingestion_result.json"):
            result = self.storage.read_model(path, GarmentIngestionResult)
            if result:
                self._results[result.input_id] = result
        for path in self.storage.glob("garment_inputs/*/metadata/request.json"):
            request = self.storage.read_model(path, GarmentIngestionRequest)
            if request:
                self._requests[request.input_id] = request

    def _load_result_from_disk(self, input_id: str) -> GarmentIngestionResult | None:
        matches = self.storage.glob(f"garment_inputs/{input_id}/metadata/ingestion_result.json")
        if not matches:
            return None
        result = self.storage.read_model(matches[0], GarmentIngestionResult)
        if result:
            self._results[input_id] = result
        return result

    def _load_request_from_disk(self, input_id: str) -> GarmentIngestionRequest | None:
        matches = self.storage.glob(f"garment_inputs/{input_id}/metadata/request.json")
        if not matches:
            return None
        request = self.storage.read_model(matches[0], GarmentIngestionRequest)
        if request:
            self._requests[input_id] = request
        return request

    @staticmethod
    def _candidate_svg(candidate) -> str:
        label = f"Candidate {candidate.candidate_id}"
        rationale = candidate.rationale or "Candidate preview placeholder."
        return (
            "<svg xmlns='http://www.w3.org/2000/svg' width='720' height='960'>"
            "<rect width='100%' height='100%' fill='#faf7f2'/>"
            "<rect x='80' y='120' width='560' height='680' rx='24' fill='#e5ddd3' stroke='#3d342b' stroke-width='2'/>"
            f"<text x='90' y='70' font-size='28' font-family='Arial'>{label}</text>"
            f"<text x='90' y='840' font-size='22' font-family='Arial'>confidence={candidate.confidence:.2f}</text>"
            f"<text x='90' y='885' font-size='20' font-family='Arial'>{json.dumps(rationale)[1:-1]}</text>"
            "</svg>"
        )
