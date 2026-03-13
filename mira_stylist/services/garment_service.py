from __future__ import annotations

from mira_stylist.models.garment import GarmentCategory, GarmentItem, ProductSource
from mira_stylist.models.garment_input import GarmentIngestionResult, GarmentSelection
from mira_stylist.pipelines import GarmentIngestionPipeline
from mira_stylist.utils.ids import new_prefixed_id

from .storage_service import AssetStorageService


class GarmentService:
    """Store canonical garments created from normalized inputs and candidate selection."""

    def __init__(
        self,
        storage: AssetStorageService,
        pipeline: GarmentIngestionPipeline | None = None,
    ):
        self.storage = storage
        self.pipeline = pipeline or GarmentIngestionPipeline()
        self._garments: dict[str, GarmentItem] = {}
        self._load_existing_garments()

    def create_from_selection(
        self,
        *,
        ingestion_result: GarmentIngestionResult,
        selection: GarmentSelection,
        title: str | None = None,
        brand: str | None = None,
        category_hint: GarmentCategory | None = None,
        color: str | None = None,
        size_info: dict[str, str] | None = None,
    ) -> GarmentItem:
        """
        Build the final garment record from the selected candidate image.

        TODO:
        - add versioned garment revisions
        - preserve derived masks and human edits across reprocessing
        """

        garment_id = new_prefixed_id("garment")
        storage_paths = self.storage.ensure_garment_paths(garment_id)
        # The pipeline receives an internal request-like structure derived from the result.
        from mira_stylist.models.garment_input import GarmentIngestionRequest

        request = GarmentIngestionRequest(
            input_id=ingestion_result.input_id,
            uploaded_by="unknown",
            title=title,
            brand=brand,
            category_hint=category_hint.value if category_hint else None,
            color=color,
            size_info=size_info or {},
        )
        source = ProductSource(
            source_url=ingestion_result.source_metadata.source_url if ingestion_result.source_metadata else None,
            referring_page_url=ingestion_result.source_metadata.referring_page_url if ingestion_result.source_metadata else None,
            domain=ingestion_result.source_metadata.domain if ingestion_result.source_metadata else None,
            brand=brand or (request.brand if request else None),
            title=title or (request.title if request else None),
            source_images=ingestion_result.source_images,
            parser_notes=ingestion_result.source_metadata.notes if ingestion_result.source_metadata else None,
            metadata=ingestion_result.source_metadata,
        )
        artifacts = self.pipeline.build_garment_assets(
            ingestion_request=request,
            ingestion_result=ingestion_result,
            selection=selection,
            storage_paths=storage_paths,
            source=source,
            category_hint=category_hint,
        )
        garment = GarmentItem(
            garment_id=garment_id,
            raw_input_id=ingestion_result.input_id,
            source=artifacts.source,
            brand=brand or (request.brand if request else None) or artifacts.source.brand,
            title=title or (request.title if request else None) or artifacts.source.title or "Imported garment",
            category=category_hint or artifacts.category,
            color=color or artifacts.color,
            size_info=size_info or (request.size_info if request else {}),
            primary_image_path=artifacts.primary_image_path,
            source_images=ingestion_result.source_images,
            assets=artifacts.assets,
            extraction_status=artifacts.extraction_status,
            confidence_scores={**ingestion_result.confidence_scores, **artifacts.confidence_scores},
        )
        self.storage.write_metadata(storage_paths.metadata_dir / "garment.json", garment)
        self.storage.write_metadata(storage_paths.metadata_dir / "build_notes.json", {"notes": artifacts.notes})
        self.storage.write_metadata(storage_paths.metadata_dir / "selection.json", selection)
        self._garments[garment_id] = garment
        return garment

    def get_garment(self, garment_id: str) -> GarmentItem | None:
        garment = self._garments.get(garment_id)
        if garment:
            return garment
        return self._load_garment_from_disk(garment_id)

    def _load_existing_garments(self) -> None:
        for path in self.storage.glob("garments/*/metadata/garment.json"):
            garment = self.storage.read_model(path, GarmentItem)
            if garment:
                self._garments[garment.garment_id] = garment

    def _load_garment_from_disk(self, garment_id: str) -> GarmentItem | None:
        matches = self.storage.glob(f"garments/{garment_id}/metadata/garment.json")
        if not matches:
            return None
        garment = self.storage.read_model(matches[0], GarmentItem)
        if garment:
            self._garments[garment_id] = garment
        return garment
