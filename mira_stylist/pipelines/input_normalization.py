from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from mira_stylist.models.garment_input import (
    GarmentCandidateImage,
    GarmentInput,
    GarmentInputStatus,
    ProductSourceMetadata,
    SourceImageRef,
)
from mira_stylist.utils.ids import new_prefixed_id
from mira_stylist.utils.paths import GarmentInputStoragePaths


@dataclass(frozen=True)
class InputNormalizationArtifacts:
    source_metadata: ProductSourceMetadata | None
    source_images: list[SourceImageRef]
    candidate_images: list[GarmentCandidateImage]
    status: GarmentInputStatus
    confidence_scores: dict[str, float]
    notes: list[str]


class InputNormalizationPipeline:
    """Normalize all image-first and URL-based inputs into a shared internal structure."""

    def normalize(
        self, garment_input: GarmentInput, storage_paths: GarmentInputStoragePaths
    ) -> InputNormalizationArtifacts:
        """
        Create candidate image records from the raw user input.

        TODO:
        - decode image dimensions and EXIF safely
        - run lightweight garment detection or segmentation proposals
        - support multi-object candidate extraction from cluttered screenshots
        - add site adapters for product-page metadata enrichment
        """

        source_metadata = self._source_metadata(garment_input)
        source_images = self._source_images(garment_input)
        candidate_images = self._build_candidates(garment_input, storage_paths, source_images)

        status = (
            GarmentInputStatus.CANDIDATE_REVIEW_REQUIRED
            if len(candidate_images) > 1 or garment_input.input_type.value in {"screenshot", "product_page_url"}
            else GarmentInputStatus.NORMALIZED
        )

        confidence_scores = {
            "single_garment_likelihood": 0.82 if garment_input.input_type.value in {"uploaded_image", "pasted_image", "image_url"} else 0.54,
            "background_complexity": 0.25 if garment_input.input_type.value in {"uploaded_image", "image_url"} else 0.63,
        }

        notes = [
            "Image-first ingestion is the primary path in this scaffold.",
            "Candidate extraction is placeholder logic, not a real detector.",
        ]
        if garment_input.input_type.value == "product_page_url":
            notes.append("Product-page ingestion is best-effort metadata enrichment only.")
        if garment_input.input_type.value == "screenshot":
            notes.append("Screenshots often require manual candidate confirmation because multiple items may be present.")

        return InputNormalizationArtifacts(
            source_metadata=source_metadata,
            source_images=source_images,
            candidate_images=candidate_images,
            status=status,
            confidence_scores=confidence_scores,
            notes=notes,
        )

    def _source_metadata(self, garment_input: GarmentInput) -> ProductSourceMetadata | None:
        url = garment_input.source_url or garment_input.referring_page_url
        if not url:
            return None
        parsed = urlparse(str(url))
        return ProductSourceMetadata(
            source_url=garment_input.source_url,
            referring_page_url=garment_input.referring_page_url,
            discovered_image_urls=garment_input.supplemental_image_urls,
            domain=parsed.netloc or None,
            parser_name="generic",
            parser_confidence=0.2 if garment_input.input_type.value == "product_page_url" else 0.0,
            notes="Generic metadata only. Add retailer-specific adapters later.",
        )

    def _source_images(self, garment_input: GarmentInput) -> list[SourceImageRef]:
        if garment_input.input_type.value == "product_page_url" and garment_input.supplemental_image_urls:
            images: list[SourceImageRef] = []
            for idx, url in enumerate(garment_input.supplemental_image_urls, start=1):
                images.append(
                    SourceImageRef(
                        image_id=new_prefixed_id("srcimg"),
                        source_url=url,
                        original_filename=f"discovered_{idx}",
                        mime_type="image/url",
                        role="discovered_source",
                        confidence=0.55,
                        notes="Discovered from product-page metadata.",
                    )
                )
            return images

        return [
            SourceImageRef(
                image_id=new_prefixed_id("srcimg"),
                source_url=garment_input.source_url if garment_input.input_type.value in {"image_url"} else None,
                local_path=garment_input.raw_asset_path if garment_input.input_type.value in {"uploaded_image", "pasted_image", "screenshot"} else None,
                original_filename=garment_input.original_filename,
                mime_type=garment_input.mime_type,
                image_width=garment_input.image_width,
                image_height=garment_input.image_height,
                role="raw_source",
                confidence=0.9,
                notes="Primary source image reference.",
            )
        ]

    def _build_candidates(
        self,
        garment_input: GarmentInput,
        storage_paths: GarmentInputStoragePaths,
        source_images: list[SourceImageRef],
    ) -> list[GarmentCandidateImage]:
        if garment_input.input_type.value == "screenshot":
            source_image_id = source_images[0].image_id
            return [
                GarmentCandidateImage(
                    candidate_id=new_prefixed_id("cand"),
                    source_image_id=source_image_id,
                    local_preview_path=str(storage_paths.normalized_dir / "candidate_region_1.svg"),
                    crop_hint="center_region",
                    confidence=0.62,
                    rationale="Placeholder suggested crop for the most salient item.",
                    requires_user_confirmation=True,
                ),
                GarmentCandidateImage(
                    candidate_id=new_prefixed_id("cand"),
                    source_image_id=source_image_id,
                    local_preview_path=str(storage_paths.normalized_dir / "full_frame.svg"),
                    crop_hint="full_frame",
                    confidence=0.48,
                    rationale="Fallback full-frame candidate for cluttered screenshots.",
                    requires_user_confirmation=True,
                ),
            ]

        if garment_input.input_type.value == "product_page_url" and len(source_images) > 1:
            return [
                GarmentCandidateImage(
                    candidate_id=new_prefixed_id("cand"),
                    source_image_id=source.image_id,
                    local_preview_path=str(storage_paths.normalized_dir / f"candidate_{index}.svg"),
                    crop_hint="full_frame",
                    confidence=0.45,
                    rationale="Candidate discovered from product-page image list.",
                    requires_user_confirmation=True,
                )
                for index, source in enumerate(source_images, start=1)
            ]

        source_image_id = source_images[0].image_id
        return [
            GarmentCandidateImage(
                candidate_id=new_prefixed_id("cand"),
                source_image_id=source_image_id,
                local_preview_path=str(storage_paths.normalized_dir / "primary_candidate.svg"),
                crop_hint="full_frame",
                confidence=0.83 if garment_input.input_type.value != "product_page_url" else 0.45,
                rationale="Assumes the provided image is already centered on a single garment.",
                requires_user_confirmation=garment_input.input_type.value == "product_page_url",
            )
        ]
