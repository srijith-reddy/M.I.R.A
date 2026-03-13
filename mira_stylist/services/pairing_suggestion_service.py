from __future__ import annotations

from mira_stylist.models import PairingRecommendation, PairingSuggestion, UserAvatar
from mira_stylist.models.garment import GarmentCategory, GarmentItem


class PairingSuggestionService:
    """Suggest outfit pairings for a single garment with occasion-aware guidance."""

    def suggest_pairings(
        self,
        *,
        avatar: UserAvatar,
        garment: GarmentItem,
        occasion: str | None,
        style_goal: str | None,
        weather_hint: str | None,
    ) -> PairingSuggestion:
        normalized_occasion = (occasion or "").strip().lower()
        normalized_goal = (style_goal or "").strip().lower()
        normalized_weather = (weather_hint or "").strip().lower()

        recommendations = self._recommendations(
            avatar=avatar,
            garment=garment,
            occasion=normalized_occasion,
            style_goal=normalized_goal,
            weather_hint=normalized_weather,
        )
        confidence_score = self._confidence_score(avatar=avatar, garment=garment)
        confidence_label = self._confidence_label(confidence_score)
        outfit_formula = [f"{garment.category.value}: {garment.title}"] + [f"{item.role}: {item.suggestion}" for item in recommendations[:3]]
        notes = [
            "Pairing suggestions are heuristic and based on garment category, color direction, avatar profile, and occasion context.",
            "Current suggestions do not inspect your real wardrobe yet, so they describe recommended complements rather than existing owned items.",
        ]
        if avatar.body_profile.posture_hint == "single_photo":
            notes.append("Because the avatar came from a quick single-photo path, proportion-sensitive layering guidance is lower confidence.")
        if normalized_weather:
            notes.append(f"Weather hint applied: {normalized_weather}.")

        return PairingSuggestion(
            avatar_id=avatar.avatar_id,
            garment_id=garment.garment_id,
            occasion=occasion,
            style_goal=style_goal,
            summary=self._summary(garment=garment, occasion=normalized_occasion, style_goal=normalized_goal),
            outfit_formula=outfit_formula,
            recommendations=recommendations,
            confidence_label=confidence_label,
            confidence_score=confidence_score,
            notes=notes,
        )

    def _recommendations(
        self,
        *,
        avatar: UserAvatar,
        garment: GarmentItem,
        occasion: str,
        style_goal: str,
        weather_hint: str,
    ) -> list[PairingRecommendation]:
        palette = self._palette_options(garment.color)
        structured = avatar.body_profile.body_frame == "broad" or style_goal in {"polished", "structured", "clean"}
        relaxed = style_goal in {"relaxed", "casual", "easy"} or occasion in {"travel", "casual", "weekend"}
        cold_weather = weather_hint in {"cold", "chilly", "rain"} or occasion in {"travel"} and garment.category != GarmentCategory.OUTERWEAR

        recommendations: list[PairingRecommendation] = []
        if garment.category == GarmentCategory.TOP:
            recommendations.extend(
                [
                    PairingRecommendation(
                        role="bottom",
                        suggested_category="bottom",
                        suggestion="tailored trousers" if structured else "straight-leg denim",
                        colors=palette["base_bottoms"],
                        rationale="A cleaner bottom balances the top and gives the look enough structure for the current occasion/style goal.",
                        priority="high",
                    ),
                    PairingRecommendation(
                        role="footwear",
                        suggested_category="footwear",
                        suggestion="sleek loafers" if structured else "clean low-profile sneakers",
                        colors=palette["footwear"],
                        rationale="Footwear should echo the polish level of the top rather than compete with it.",
                        priority="high",
                    ),
                ]
            )
            if cold_weather:
                recommendations.append(
                    PairingRecommendation(
                        role="layer",
                        suggested_category="outerwear",
                        suggestion="minimal wool coat" if structured else "cropped jacket",
                        colors=palette["outerwear"],
                        rationale="An outer layer helps the outfit feel intentional while keeping the silhouette cohesive.",
                        priority="medium",
                    )
                )
        elif garment.category == GarmentCategory.BOTTOM:
            recommendations.extend(
                [
                    PairingRecommendation(
                        role="top",
                        suggested_category="top",
                        suggestion="fitted knit top" if structured else "soft tucked tee",
                        colors=palette["tops"],
                        rationale="A balanced top prevents the outfit from feeling bottom-heavy.",
                        priority="high",
                    ),
                    PairingRecommendation(
                        role="footwear",
                        suggested_category="footwear",
                        suggestion="ankle boots" if structured else "simple sneakers",
                        colors=palette["footwear"],
                        rationale="Footwear should anchor the lower half without visually shortening the leg line.",
                        priority="high",
                    ),
                ]
            )
        elif garment.category == GarmentCategory.DRESS:
            recommendations.extend(
                [
                    PairingRecommendation(
                        role="footwear",
                        suggested_category="footwear",
                        suggestion="heeled boots" if structured else "clean sandals",
                        colors=palette["footwear"],
                        rationale="Footwear should either sharpen the dress or keep it light depending on the styling goal.",
                        priority="high",
                    ),
                    PairingRecommendation(
                        role="layer",
                        suggested_category="outerwear",
                        suggestion="structured blazer" if structured else "soft cardigan",
                        colors=palette["outerwear"],
                        rationale="A topper gives the dress more occasion flexibility and helps frame the silhouette.",
                        priority="medium",
                    ),
                ]
            )
        elif garment.category == GarmentCategory.OUTERWEAR:
            recommendations.extend(
                [
                    PairingRecommendation(
                        role="base_layer",
                        suggested_category="top",
                        suggestion="fitted neutral knit" if structured else "lightweight tee or tank",
                        colors=palette["tops"],
                        rationale="A simpler base layer prevents the outerwear from competing with another strong silhouette.",
                        priority="high",
                    ),
                    PairingRecommendation(
                        role="bottom",
                        suggested_category="bottom",
                        suggestion="wide-leg trousers" if structured else "straight denim",
                        colors=palette["base_bottoms"],
                        rationale="The bottom should support the coat or jacket rather than create a second focal point.",
                        priority="high",
                    ),
                ]
            )
        elif garment.category == GarmentCategory.FOOTWEAR:
            recommendations.extend(
                [
                    PairingRecommendation(
                        role="bottom",
                        suggested_category="bottom",
                        suggestion="cropped trousers" if structured else "relaxed denim",
                        colors=palette["base_bottoms"],
                        rationale="Bottom hem shape matters more when footwear is the focal piece.",
                        priority="high",
                    ),
                    PairingRecommendation(
                        role="top",
                        suggested_category="top",
                        suggestion="clean tucked shirt" if structured else "easy knit top",
                        colors=palette["tops"],
                        rationale="A quieter top lets the shoe choice read clearly.",
                        priority="medium",
                    ),
                ]
            )
        else:
            recommendations.extend(
                [
                    PairingRecommendation(
                        role="base",
                        suggested_category="top",
                        suggestion="simple neutral base layer",
                        colors=palette["tops"],
                        rationale="A neutral anchor gives the current item room to lead the look.",
                        priority="high",
                    ),
                    PairingRecommendation(
                        role="support",
                        suggested_category="bottom",
                        suggestion="clean straight-leg bottom",
                        colors=palette["base_bottoms"],
                        rationale="A simple bottom keeps the overall silhouette readable.",
                        priority="medium",
                    ),
                ]
            )

        if garment.category != GarmentCategory.ACCESSORY:
            recommendations.append(
                PairingRecommendation(
                    role="accessory",
                    suggested_category="accessory",
                    suggestion="minimal jewelry and a compact bag" if structured else "soft everyday accessory stack",
                    colors=palette["accessories"],
                    rationale="Accessories should reinforce the styling direction rather than introduce a competing story.",
                    priority="medium",
                )
            )
        if relaxed and recommendations:
            recommendations[0].rationale += " A slightly more relaxed version also fits the current styling goal."
        return recommendations[:4]

    @staticmethod
    def _palette_options(color: str | None) -> dict[str, list[str]]:
        if not color:
            return {
                "base_bottoms": ["black", "charcoal", "dark denim"],
                "tops": ["cream", "white", "soft gray"],
                "footwear": ["black", "tan"],
                "outerwear": ["camel", "black", "stone"],
                "accessories": ["gold", "silver", "black leather"],
            }
        tone = color.lower()
        if "black" in tone:
            accent = ["cream", "stone", "deep olive"]
        elif "blue" in tone:
            accent = ["white", "charcoal", "tan"]
        elif "green" in tone:
            accent = ["cream", "brown", "black"]
        elif "red" in tone:
            accent = ["black", "stone", "dark denim"]
        else:
            accent = ["black", "cream", "camel"]
        return {
            "base_bottoms": accent,
            "tops": accent,
            "footwear": ["black", "brown", accent[0]],
            "outerwear": ["camel", "stone", "black"],
            "accessories": ["gold", "silver", accent[0]],
        }

    @staticmethod
    def _confidence_score(*, avatar: UserAvatar, garment: GarmentItem) -> float:
        avatar_signal = min(avatar.body_profile.profile_confidence * 0.42, 0.32)
        garment_signal = min(garment.confidence_scores.get("candidate_selection", 0.0) * 0.36, 0.28)
        category_signal = 0.12 if garment.category != GarmentCategory.UNKNOWN else 0.04
        return round(max(0.3, min(0.18 + avatar_signal + garment_signal + category_signal, 0.82)), 2)

    @staticmethod
    def _confidence_label(score: float) -> str:
        if score >= 0.7:
            return "high"
        if score >= 0.5:
            return "medium"
        return "low"

    @staticmethod
    def _summary(*, garment: GarmentItem, occasion: str, style_goal: str) -> str:
        if occasion and style_goal:
            return f"For {occasion}, I would build around this {garment.category.value} with a {style_goal} direction and keep the supporting pieces simpler than the anchor item."
        if occasion:
            return f"For {occasion}, this {garment.category.value} can anchor the outfit if the supporting pieces stay balanced and not overly busy."
        if style_goal:
            return f"To push this look toward a {style_goal} direction, I would pair this item with cleaner, quieter supporting pieces."
        return f"This {garment.category.value} works best as the anchor of a small, well-balanced outfit formula."
