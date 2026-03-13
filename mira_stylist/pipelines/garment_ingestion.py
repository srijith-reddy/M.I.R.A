from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from mira_stylist.models.garment import GarmentAssetManifest, GarmentCategory, GarmentProcessingStatus, ProductSource
from mira_stylist.models.garment_input import (
    GarmentCandidateImage,
    GarmentIngestionRequest,
    GarmentIngestionResult,
    GarmentSelection,
)
from mira_stylist.utils.paths import GarmentStoragePaths


@dataclass(frozen=True)
class GarmentExtractionArtifacts:
    source: ProductSource
    category: GarmentCategory
    color: str | None
    assets: GarmentAssetManifest
    extraction_status: GarmentProcessingStatus
    primary_image_path: str
    confidence_scores: dict[str, float]
    notes: list[str]


class GarmentIngestionPipeline:
    """Stub pipeline that turns a selected candidate into a canonical garment record."""

    def build_garment_assets(
        self,
        ingestion_request: GarmentIngestionRequest,
        ingestion_result: GarmentIngestionResult,
        selection: GarmentSelection,
        storage_paths: GarmentStoragePaths,
        source: ProductSource,
        category_hint: GarmentCategory | None = None,
    ) -> GarmentExtractionArtifacts:
        """
        Create the canonical garment asset manifest from the selected source candidate.

        TODO:
        - run segmentation on the chosen candidate image
        - infer garment category and attributes with CV models
        - generate proxy mesh or category-specific garment template
        - preserve provenance between selected candidate and downstream assets
        """

        assets = GarmentAssetManifest(
            raw_asset_dir=str(storage_paths.raw_dir),
            candidates_dir=str(storage_paths.candidates_dir),
            primary_image_path=str(storage_paths.raw_dir / "selected_primary_image.svg"),
            segmented_asset_path=str(storage_paths.segmented_dir / "garment_mask.json"),
            mesh_path=str(storage_paths.mesh_dir / "garment_proxy.glb"),
            texture_path=str(storage_paths.textures_dir / "garment_texture.json"),
            preview_image_path=str(storage_paths.candidates_dir / "preview_reference.svg"),
            metadata_path=str(storage_paths.metadata_dir / "garment_manifest.json"),
        )

        primary_candidate = next(
            (
                candidate
                for candidate in ingestion_result.candidate_images
                if candidate.candidate_id == selection.selected_candidate_id
            ),
            None,
        )
        if assets.primary_image_path:
            Path(assets.primary_image_path).write_text(
                self._primary_image_svg(source.title or "Garment", primary_candidate.confidence if primary_candidate else 0.0),
                encoding="utf-8",
            )
        if assets.preview_image_path:
            Path(assets.preview_image_path).write_text(
                self._primary_image_svg(source.title or "Preview", primary_candidate.confidence if primary_candidate else 0.0),
                encoding="utf-8",
            )
        if assets.segmented_asset_path:
            Path(assets.segmented_asset_path).write_text(
                json.dumps(
                    {
                        "status": "placeholder",
                        "selected_candidate_id": selection.selected_candidate_id,
                        "note": "Segmentation is not implemented; this file marks where a mask artifact would land.",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        if assets.texture_path:
            Path(assets.texture_path).write_text(
                json.dumps(
                    {
                        "status": "placeholder",
                        "note": "Texture extraction is a future advanced stage.",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        if assets.mesh_path:
            Path(assets.mesh_path).write_text("# placeholder garment proxy mesh\n", encoding="utf-8")

        notes = [
            "Canonical garment created from normalized input candidate.",
            "Segmentation, category inference, and reconstruction are still placeholders.",
        ]
        inferred_category, category_confidence = self._infer_category(
            ingestion_request=ingestion_request,
            ingestion_result=ingestion_result,
            category_hint=category_hint,
        )
        inferred_color, color_confidence = self._infer_color(
            ingestion_request=ingestion_request,
            ingestion_result=ingestion_result,
        )
        if category_hint:
            notes.append(f"Category override supplied by client: {category_hint.value}.")
        elif inferred_category != GarmentCategory.UNKNOWN:
            notes.append(f"Category inferred heuristically as {inferred_category.value}.")
        else:
            notes.append("Category could not be inferred confidently; generic styling fallbacks will be used.")
        if ingestion_request.color:
            notes.append(f"Color override supplied by client: {ingestion_request.color}.")
        elif inferred_color:
            notes.append(f"Color inferred heuristically as {inferred_color}.")

        return GarmentExtractionArtifacts(
            source=source,
            category=inferred_category,
            color=ingestion_request.color or inferred_color,
            assets=assets,
            extraction_status=GarmentProcessingStatus.SEGMENTATION_PENDING,
            primary_image_path=assets.primary_image_path or "",
            confidence_scores={
                "candidate_selection": next(
                    (
                        candidate.confidence
                        for candidate in ingestion_result.candidate_images
                        if candidate.candidate_id == selection.selected_candidate_id
                    ),
                    0.0,
                ),
                "attribute_stub_confidence": 0.35,
                "category_inference": category_confidence,
                "color_inference": color_confidence,
            },
            notes=notes,
        )

    @staticmethod
    def _infer_category(
        *,
        ingestion_request: GarmentIngestionRequest,
        ingestion_result: GarmentIngestionResult,
        category_hint: GarmentCategory | None,
    ) -> tuple[GarmentCategory, float]:
        if category_hint:
            return category_hint, 0.95
        text_parts = [ingestion_request.title or "", ingestion_request.notes or ""]
        for source_image in ingestion_result.source_images:
            if source_image.original_filename:
                text_parts.append(source_image.original_filename)
        normalized = " ".join(text_parts).lower()
        keyword_map = {
            GarmentCategory.OUTERWEAR: ["jacket", "blazer", "coat", "trench", "parka", "bomber", "outerwear"],
            GarmentCategory.TOP: ["shirt", "tee", "t-shirt", "top", "blouse", "sweater", "knit", "hoodie", "cardigan"],
            GarmentCategory.BOTTOM: ["pant", "pants", "trouser", "trousers", "jean", "jeans", "skirt", "shorts"],
            GarmentCategory.DRESS: ["dress", "gown", "slipdress", "maxi", "midi"],
            GarmentCategory.FOOTWEAR: ["shoe", "shoes", "boot", "boots", "sneaker", "sneakers", "heel", "heels", "loafer"],
            GarmentCategory.ACCESSORY: ["bag", "belt", "hat", "scarf", "necklace", "ring", "earring", "watch"],
        }
        for category, keywords in keyword_map.items():
            if any(keyword in normalized for keyword in keywords):
                return category, 0.78
        return GarmentCategory.UNKNOWN, 0.24

    @staticmethod
    def _infer_color(
        *,
        ingestion_request: GarmentIngestionRequest,
        ingestion_result: GarmentIngestionResult,
    ) -> tuple[str | None, float]:
        if ingestion_request.color:
            return ingestion_request.color, 0.95
        text_parts = [ingestion_request.title or "", ingestion_request.notes or ""]
        for source_image in ingestion_result.source_images:
            if source_image.original_filename:
                text_parts.append(source_image.original_filename)
        normalized = " ".join(text_parts).lower()
        color_keywords = [
            "black",
            "white",
            "cream",
            "beige",
            "camel",
            "tan",
            "brown",
            "blue",
            "navy",
            "red",
            "burgundy",
            "green",
            "olive",
            "pink",
            "yellow",
            "orange",
            "purple",
            "gray",
            "grey",
        ]
        for color in color_keywords:
            if color in normalized:
                return color, 0.74
        return None, 0.18

    @staticmethod
    def _primary_image_svg(label: str, confidence: float) -> str:
        return (
            "<svg xmlns='http://www.w3.org/2000/svg' width='768' height='1024'>"
            "<rect width='100%' height='100%' fill='#f6f0e6'/>"
            "<rect x='144' y='130' width='480' height='700' rx='32' fill='#d7c8b7' stroke='#4f453d' stroke-width='2'/>"
            f"<text x='120' y='80' font-size='30' font-family='Arial'>{label}</text>"
            f"<text x='120' y='900' font-size='22' font-family='Arial'>selected candidate confidence={confidence:.2f}</text>"
            "<text x='120' y='940' font-size='18' font-family='Arial'>This is an MVP placeholder, not a segmented garment asset.</text>"
            "</svg>"
        )
