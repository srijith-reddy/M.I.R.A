from __future__ import annotations

from mira_stylist.models import (
    GeneratedOutfit,
    OutfitComponent,
    OutfitComponentSourceKind,
    OutfitGenerationRequest,
    PairingSuggestion,
    PreviewStatus,
    RenderMode,
    UserAvatar,
)
from mira_stylist.models.garment import GarmentCategory, GarmentItem
from mira_stylist.models.common import utc_now
from mira_stylist.pipelines import PreviewGenerationPipeline
from mira_stylist.utils.ids import new_prefixed_id

from .storage_service import AssetStorageService
from .stylist_commentary_service import StylistCommentaryService


class OutfitGenerationService:
    """Generate and persist composed multi-garment outfit proposals."""

    def __init__(
        self,
        storage: AssetStorageService,
        pipeline: PreviewGenerationPipeline | None = None,
        commentary: StylistCommentaryService | None = None,
    ):
        self.storage = storage
        self.pipeline = pipeline or PreviewGenerationPipeline()
        self.commentary = commentary or StylistCommentaryService()
        self._outfits: dict[str, GeneratedOutfit] = {}
        self._load_existing_outfits()

    def generate_outfit(
        self,
        request: OutfitGenerationRequest,
        *,
        avatar: UserAvatar,
        anchor_garment: GarmentItem,
    ) -> GeneratedOutfit:
        pairing = self.commentary.suggest_pairings(
            avatar=avatar,
            garment=anchor_garment,
            occasion=request.occasion,
            style_goal=request.style_goal,
            weather_hint=request.weather_hint,
        )
        outfit_id = new_prefixed_id("outfit")
        storage_paths = self.storage.ensure_outfit_paths(outfit_id)
        components = self._build_components(
            outfit_id=outfit_id,
            anchor_garment=anchor_garment,
            pairing=pairing,
        )
        asset_paths = self.pipeline.create_outfit_preview(
            outfit_id=outfit_id,
            avatar=avatar,
            anchor_garment=anchor_garment,
            components=components,
            storage_paths=storage_paths,
            render_mode=request.render_mode,
            camera_angle=request.camera_angle.value,
            occasion=request.occasion,
            style_goal=request.style_goal,
        )
        outfit = GeneratedOutfit(
            outfit_id=outfit_id,
            avatar_id=avatar.avatar_id,
            anchor_garment_id=anchor_garment.garment_id,
            occasion=request.occasion,
            style_goal=request.style_goal,
            weather_hint=request.weather_hint,
            summary=pairing.summary,
            outfit_formula=pairing.outfit_formula,
            components=components,
            confidence_label=pairing.confidence_label,
            confidence_score=pairing.confidence_score,
            preview_status=PreviewStatus.COMPLETED,
            output_asset_paths=asset_paths,
            notes=[
                "This outfit preview is composed from one real anchor garment plus generated companion pieces.",
                "Generated companion pieces are stylist placeholders until wardrobe memory or multi-garment assets exist.",
                f"Requested render mode: {request.render_mode.value}",
                f"Requested camera angle: {request.camera_angle.value}",
            ]
            + pairing.notes,
        )
        self.storage.write_metadata(storage_paths.metadata_dir / "outfit.json", outfit)
        self._outfits[outfit_id] = outfit
        return outfit

    def get_outfit(self, outfit_id: str) -> GeneratedOutfit | None:
        outfit = self._outfits.get(outfit_id)
        if outfit:
            return outfit
        matches = self.storage.glob(f"outfits/{outfit_id}/metadata/outfit.json")
        if not matches:
            return None
        outfit = self.storage.read_model(matches[0], GeneratedOutfit)
        if not outfit:
            return None
        self._outfits[outfit_id] = outfit
        return outfit

    def _load_existing_outfits(self) -> None:
        for path in self.storage.glob("outfits/*/metadata/outfit.json"):
            outfit = self.storage.read_model(path, GeneratedOutfit)
            if outfit:
                self._outfits[outfit.outfit_id] = outfit

    def _build_components(
        self,
        *,
        outfit_id: str,
        anchor_garment: GarmentItem,
        pairing: PairingSuggestion,
    ) -> list[OutfitComponent]:
        components = [
            OutfitComponent(
                component_id=f"{outfit_id}_anchor",
                source_kind=OutfitComponentSourceKind.ANCHOR_GARMENT,
                source_garment_id=anchor_garment.garment_id,
                role="anchor",
                category=anchor_garment.category,
                label=anchor_garment.title,
                color=anchor_garment.color,
                layer_order=self._layer_order(anchor_garment.category),
                rationale="Primary garment selected by the user.",
                locked=True,
            )
        ]
        seen_categories = {anchor_garment.category.value}
        for index, rec in enumerate(pairing.recommendations, start=1):
            category = self._category_from_value(rec.suggested_category)
            if category.value in seen_categories and category != GarmentCategory.ACCESSORY:
                continue
            seen_categories.add(category.value)
            components.append(
                OutfitComponent(
                    component_id=f"{outfit_id}_companion_{index}",
                    source_kind=OutfitComponentSourceKind.GENERATED_COMPANION,
                    role=rec.role,
                    category=category,
                    label=rec.suggestion,
                    color=rec.colors[0] if rec.colors else None,
                    layer_order=self._layer_order(category),
                    rationale=rec.rationale,
                    locked=False,
                )
            )
        components.sort(key=lambda component: component.layer_order)
        return components

    @staticmethod
    def _category_from_value(value: str) -> GarmentCategory:
        for category in GarmentCategory:
            if category.value == value:
                return category
        return GarmentCategory.UNKNOWN

    @staticmethod
    def _layer_order(category: GarmentCategory) -> int:
        ordering = {
            GarmentCategory.FOOTWEAR: 5,
            GarmentCategory.BOTTOM: 10,
            GarmentCategory.TOP: 20,
            GarmentCategory.DRESS: 25,
            GarmentCategory.OUTERWEAR: 40,
            GarmentCategory.ACCESSORY: 50,
            GarmentCategory.UNKNOWN: 30,
        }
        return ordering.get(category, 30)
