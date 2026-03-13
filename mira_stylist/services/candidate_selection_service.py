from __future__ import annotations

from mira_stylist.models.garment import GarmentCategory, GarmentItem
from mira_stylist.models.garment_input import GarmentSelection

from .garment_service import GarmentService
from .ingestion_service import GarmentIngestionService


class CandidateSelectionService:
    """Resolve manual candidate selection into a canonical garment record."""

    def __init__(self, ingestion: GarmentIngestionService, garments: GarmentService):
        self.ingestion = ingestion
        self.garments = garments

    def select_candidate(
        self,
        *,
        selection: GarmentSelection,
        title: str | None = None,
        brand: str | None = None,
        category_hint: GarmentCategory | None = None,
        color: str | None = None,
        size_info: dict[str, str] | None = None,
    ) -> GarmentItem:
        """
        Promote a chosen candidate image into the canonical garment catalog.

        TODO:
        - support human-in-the-loop crop corrections
        - allow multi-layer garment selection from a single screenshot
        """

        result = self.ingestion.get_result(selection.input_id)
        if result is None:
            raise ValueError("Garment ingestion result not found.")
        if not any(c.candidate_id == selection.selected_candidate_id for c in result.candidate_images):
            raise ValueError("Selected candidate was not found for this input.")

        request = self.ingestion.get_request(selection.input_id)
        resolved_category = category_hint or self.ingestion.get_request_category(selection.input_id)

        return self.garments.create_from_selection(
            ingestion_result=result,
            selection=selection,
            title=title or (request.title if request else None),
            brand=brand or (request.brand if request else None),
            category_hint=resolved_category,
            color=color,
            size_info=size_info or (request.size_info if request else {}),
        )
