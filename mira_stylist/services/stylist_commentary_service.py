from __future__ import annotations

from mira_stylist.models import (
    FitAssessment,
    LookComparisonFeedback,
    PairingRecommendation,
    PairingSuggestion,
    SingleLookFeedback,
    StylistCommentary,
    TryOnRequest,
    UserAvatar,
)
from mira_stylist.models.garment import GarmentCategory, GarmentItem


class StylistCommentaryService:
    """Generate uncertainty-aware stylist commentary for a preview result."""

    def build_commentary(
        self,
        *,
        avatar: UserAvatar,
        garment: GarmentItem,
        request: TryOnRequest,
        fit_assessment: FitAssessment,
    ) -> StylistCommentary:
        confidence_score = self._commentary_confidence(avatar, garment, fit_assessment)
        confidence_label = self._confidence_label(confidence_score)

        what_works = self._what_works(avatar=avatar, garment=garment, request=request, fit=fit_assessment)
        watch_outs = self._watch_outs(avatar=avatar, garment=garment, request=request, fit=fit_assessment)
        fit_caveats = self._fit_caveats(avatar=avatar, garment=garment, fit=fit_assessment)
        summary = self._summary(garment=garment, confidence_label=confidence_label, what_works=what_works, watch_outs=watch_outs)

        notes = [
            "Stylist commentary is heuristic and should be treated as guidance, not a fit guarantee.",
            "Current reasoning uses avatar profile, garment metadata, and preview confidence rather than true garment simulation.",
        ]
        if avatar.body_profile.posture_hint in {"single_photo", "cropped", "turned"}:
            notes.append("Avatar input quality is limited, so silhouette guidance is less certain than usual.")
        if garment.confidence_scores.get("candidate_selection", 0.0) < 0.55:
            notes.append("Garment extraction confidence is moderate, so styling notes may reflect imperfect source interpretation.")

        return StylistCommentary(
            summary=summary,
            what_works=what_works,
            watch_outs=watch_outs,
            fit_caveats=fit_caveats,
            confidence_label=confidence_label,
            confidence_score=confidence_score,
            tone=self._tone(confidence_score),
            notes=notes,
        )

    def answer_single_look_question(
        self,
        *,
        job_id: str,
        avatar: UserAvatar,
        garment: GarmentItem,
        request: TryOnRequest,
        commentary: StylistCommentary,
        question: str | None,
        occasion: str | None,
        style_goal: str | None,
    ) -> SingleLookFeedback:
        normalized_question = (question or "").strip().lower()
        normalized_occasion = (occasion or "").strip().lower()
        normalized_goal = (style_goal or "").strip().lower()
        answer = self._feedback_answer(
            garment=garment,
            commentary=commentary,
            question=normalized_question,
            occasion=normalized_occasion,
            style_goal=normalized_goal,
        )

        supporting_points = list(commentary.what_works[:2])
        cautions = list(commentary.watch_outs[:2] + commentary.fit_caveats[:1])
        follow_up_suggestions = self._follow_ups(
            garment=garment,
            question=normalized_question,
            occasion=normalized_occasion,
            style_goal=normalized_goal,
            commentary=commentary,
            avatar=avatar,
        )
        notes = [
            "Single-look feedback is grounded in the stored preview job and current heuristic commentary.",
            "The answer is style-oriented guidance rather than a guarantee of exact fit, comfort, or fabric behavior.",
        ]
        if avatar.body_profile.posture_hint == "single_photo":
            notes.append("Because this look uses a single-photo avatar path, body-depth judgments are especially approximate.")

        return SingleLookFeedback(
            job_id=job_id,
            question=question,
            occasion=occasion,
            answer=answer,
            confidence_label=commentary.confidence_label,
            confidence_score=commentary.confidence_score,
            supporting_points=supporting_points,
            cautions=cautions,
            follow_up_suggestions=follow_up_suggestions,
            notes=notes,
        )

    def compare_looks(
        self,
        *,
        primary_job_id: str,
        secondary_job_id: str,
        primary_avatar: UserAvatar,
        secondary_avatar: UserAvatar,
        primary_garment: GarmentItem,
        secondary_garment: GarmentItem,
        primary_commentary: StylistCommentary,
        secondary_commentary: StylistCommentary,
        primary_request: TryOnRequest,
        secondary_request: TryOnRequest,
        occasion: str | None,
        style_goal: str | None,
    ) -> LookComparisonFeedback:
        primary_score = self._comparison_score(
            avatar=primary_avatar,
            garment=primary_garment,
            commentary=primary_commentary,
            request=primary_request,
            occasion=occasion,
            style_goal=style_goal,
        )
        secondary_score = self._comparison_score(
            avatar=secondary_avatar,
            garment=secondary_garment,
            commentary=secondary_commentary,
            request=secondary_request,
            occasion=occasion,
            style_goal=style_goal,
        )

        if primary_score >= secondary_score:
            winner_job_id = primary_job_id
            winner_label = "Look A"
            loser_label = "Look B"
            winner_commentary = primary_commentary
        else:
            winner_job_id = secondary_job_id
            winner_label = "Look B"
            loser_label = "Look A"
            winner_commentary = secondary_commentary

        confidence_score = round(max(0.28, min(abs(primary_score - secondary_score) + max(primary_commentary.confidence_score, secondary_commentary.confidence_score) * 0.45, 0.87)), 2)
        confidence_label = self._confidence_label(confidence_score)
        verdict = self._comparison_verdict(
            winner_label=winner_label,
            loser_label=loser_label,
            occasion=occasion,
            style_goal=style_goal,
            winner_commentary=winner_commentary,
        )
        decision_factors = self._decision_factors(
            primary_commentary=primary_commentary,
            secondary_commentary=secondary_commentary,
            primary_garment=primary_garment,
            secondary_garment=secondary_garment,
            occasion=occasion,
            style_goal=style_goal,
        )
        cautions = list(dict.fromkeys(primary_commentary.watch_outs[:1] + secondary_commentary.watch_outs[:1] + primary_commentary.fit_caveats[:1] + secondary_commentary.fit_caveats[:1]))
        notes = [
            "Comparison is grounded in the two stored preview jobs and their commentary rather than a fresh generative pass.",
            "Treat the winner as a directionally stronger option, not an objective truth about personal taste or exact fit.",
        ]
        return LookComparisonFeedback(
            primary_job_id=primary_job_id,
            secondary_job_id=secondary_job_id,
            occasion=occasion,
            style_goal=style_goal,
            winner_job_id=winner_job_id,
            verdict=verdict,
            confidence_label=confidence_label,
            confidence_score=confidence_score,
            primary_strengths=primary_commentary.what_works[:3],
            secondary_strengths=secondary_commentary.what_works[:3],
            decision_factors=decision_factors,
            cautions=cautions[:4],
            notes=notes,
        )

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
        confidence_score = self._pairing_confidence(avatar=avatar, garment=garment)
        confidence_label = self._confidence_label(confidence_score)
        recommendations = self._pairing_recommendations(
            avatar=avatar,
            garment=garment,
            occasion=normalized_occasion,
            style_goal=normalized_goal,
            weather_hint=normalized_weather,
        )
        outfit_formula = self._outfit_formula(
            garment=garment,
            recommendations=recommendations,
            occasion=normalized_occasion,
        )
        summary = self._pairing_summary(
            garment=garment,
            recommendations=recommendations,
            occasion=normalized_occasion,
            style_goal=normalized_goal,
        )
        notes = [
            "Pairing suggestions are generic styling guidance, not wardrobe-aware recommendations yet.",
            "Once wardrobe memory exists, MIRA can swap these with suggestions grounded in pieces you actually own.",
        ]
        if normalized_weather:
            notes.append(f"Weather weighting is light in the MVP and currently treats '{weather_hint}' as a hint rather than a hard rule.")
        if avatar.body_profile.posture_hint == "single_photo":
            notes.append("Because this avatar came from one photo, proportion-driven pairing notes are more approximate than guided capture or scan-beta paths.")

        return PairingSuggestion(
            avatar_id=avatar.avatar_id,
            garment_id=garment.garment_id,
            occasion=occasion,
            style_goal=style_goal,
            weather_hint=weather_hint,
            summary=summary,
            outfit_formula=outfit_formula,
            recommendations=recommendations,
            confidence_label=confidence_label,
            confidence_score=confidence_score,
            notes=notes,
        )

    @staticmethod
    def _commentary_confidence(avatar: UserAvatar, garment: GarmentItem, fit: FitAssessment) -> float:
        avatar_signal = min(avatar.body_profile.profile_confidence * 0.45, 0.35)
        garment_signal = min(garment.confidence_scores.get("candidate_selection", 0.0) * 0.35, 0.28)
        fit_signal = min(fit.fit_confidence * 0.4, 0.32)
        penalty = 0.0
        if avatar.body_profile.posture_hint == "single_photo":
            penalty += 0.08
        if fit.occlusion_risk == "high":
            penalty += 0.06
        return round(max(0.22, min(avatar_signal + garment_signal + fit_signal + 0.16 - penalty, 0.86)), 2)

    @staticmethod
    def _confidence_label(score: float) -> str:
        if score >= 0.7:
            return "high"
        if score >= 0.5:
            return "medium"
        return "low"

    @staticmethod
    def _tone(score: float) -> str:
        if score >= 0.68:
            return "confident"
        if score >= 0.48:
            return "balanced"
        return "cautious"

    def _summary(
        self,
        *,
        garment: GarmentItem,
        confidence_label: str,
        what_works: list[str],
        watch_outs: list[str],
    ) -> str:
        if what_works and not watch_outs:
            return f"This {garment.category.value} looks directionally strong in the current preview, with {confidence_label} commentary confidence."
        if what_works and watch_outs:
            return f"This {garment.category.value} has a workable silhouette in the preview, but there are a few areas to sanity-check before trusting the result."
        return f"This preview is useful for a quick style read, but the current signals are too weak to make a stronger recommendation."

    def _what_works(
        self,
        *,
        avatar: UserAvatar,
        garment: GarmentItem,
        request: TryOnRequest,
        fit: FitAssessment,
    ) -> list[str]:
        works: list[str] = []
        if garment.category in {GarmentCategory.TOP, GarmentCategory.OUTERWEAR} and avatar.body_profile.shoulder_scale >= 0.95:
            works.append("The upper-body proportion reads balanced, so this piece should sit reasonably well through the shoulders in the preview.")
        if garment.category in {GarmentCategory.BOTTOM, GarmentCategory.DRESS} and avatar.body_profile.leg_length_ratio >= 0.47:
            works.append("The preview suggests the lower silhouette has enough vertical length to avoid looking visually shortened.")
        if garment.category == GarmentCategory.OUTERWEAR:
            works.append("Outerwear is a forgiving category for MVP previews, so the layer shape is easier to judge than exact body-hugging garments.")
        if garment.color:
            works.append(f"The {garment.color.lower()} tone is distinctive enough that the preview gives a clear style-direction signal even without realistic fabric rendering.")
        if request.camera_angle.value == "front":
            works.append("The front camera angle is the strongest current view for judging overall outfit balance.")
        if fit.fit_confidence >= 0.66:
            works.append("The current avatar and garment signals line up well enough to make this a meaningful style preview rather than pure guesswork.")
        return works[:3]

    def _watch_outs(
        self,
        *,
        avatar: UserAvatar,
        garment: GarmentItem,
        request: TryOnRequest,
        fit: FitAssessment,
    ) -> list[str]:
        watch_outs: list[str] = []
        if avatar.body_profile.posture_hint == "single_photo":
            watch_outs.append("This avatar came from one photo, so depth and side-body proportions are estimated rather than observed.")
        if fit.occlusion_risk == "high":
            watch_outs.append("The garment source looks visually noisy, so edge placement and silhouette width may be less stable than usual.")
        if garment.category in {GarmentCategory.BOTTOM, GarmentCategory.DRESS}:
            watch_outs.append("Hem length and hip behavior are still rough in the current renderer, so trust the general vibe more than the exact drape.")
        if request.render_mode.value == "wireframe":
            watch_outs.append("Wireframe mode is best for shape reading only and should not be used for fabric or coverage judgments.")
        return watch_outs[:3]

    def _fit_caveats(self, *, avatar: UserAvatar, garment: GarmentItem, fit: FitAssessment) -> list[str]:
        caveats: list[str] = []
        if fit.estimated_size_alignment in {"unknown", "unverified"}:
            caveats.append("Size alignment is still unverified because the garment metadata does not include a trustworthy fit profile.")
        if avatar.body_profile.profile_confidence < 0.55:
            caveats.append("Avatar body proportions are still heuristic, so fit notes should be treated as low-to-medium confidence.")
        if garment.category == GarmentCategory.ACCESSORY:
            caveats.append("Accessories can be judged visually, but placement and scale are not yet personalized with accessory-specific anchors.")
        if garment.category == GarmentCategory.FOOTWEAR:
            caveats.append("Footwear previewing is especially approximate because stance and foot angle are not modeled yet.")
        if not caveats:
            caveats.append("This is a style-oriented preview first; exact tension, stretch, and drape are not modeled.")
        return caveats[:3]

    def _feedback_answer(
        self,
        *,
        garment: GarmentItem,
        commentary: StylistCommentary,
        question: str,
        occasion: str,
        style_goal: str,
    ) -> str:
        if "flatter" in question or "flattering" in question:
            if commentary.confidence_score >= 0.6:
                return "Directionally, yes. The current preview suggests the silhouette is reasonably balanced on your profile, but the judgment is still approximate rather than exact."
            return "Possibly, but I would treat this as a low-to-medium confidence read because the current preview is still heuristic."
        if "formal" in question or "dressy" in question or occasion in {"dinner", "date", "wedding", "event"}:
            return self._occasion_answer(garment=garment, occasion=occasion or question, commentary=commentary)
        if "color" in question:
            return "The color direction is one of the more reliable parts of the current preview. You can trust the overall tone more than the exact fit or drape."
        if "buy" in question or "worth it" in question:
            if commentary.confidence_score >= 0.65:
                return "As a style signal, this looks promising enough to keep considering. I would still validate sizing and fabric behavior before treating it as a confident buy."
            return "I would not treat this preview alone as a strong buy signal yet. It is useful for style direction, but the current confidence is still limited."
        if style_goal:
            return f"For a {style_goal} goal, this look seems directionally compatible, but I would use the current result as a styling guide rather than a final verdict."
        return commentary.summary

    def _occasion_answer(self, *, garment: GarmentItem, occasion: str, commentary: StylistCommentary) -> str:
        occasion_text = occasion or "the occasion you mentioned"
        if garment.category in {GarmentCategory.OUTERWEAR, GarmentCategory.DRESS}:
            return f"For {occasion_text}, this reads plausible in the preview because that category carries a strong silhouette signal. I would still validate polish and fit details in real life."
        if garment.category == GarmentCategory.TOP:
            return f"For {occasion_text}, this depends more on what you pair it with. The top itself looks workable, but the full outfit context will matter."
        return f"For {occasion_text}, the look is directionally usable, but I would want more context or another angle before making a stronger recommendation."

    def _follow_ups(
        self,
        *,
        garment: GarmentItem,
        question: str,
        occasion: str,
        style_goal: str,
        commentary: StylistCommentary,
        avatar: UserAvatar,
    ) -> list[str]:
        suggestions: list[str] = []
        if not occasion:
            suggestions.append("Ask MIRA whether this works for a specific occasion like work, dinner, or travel.")
        if garment.category in {GarmentCategory.TOP, GarmentCategory.OUTERWEAR}:
            suggestions.append("Try asking what bottoms or shoes would balance this look better.")
        if commentary.confidence_label == "low":
            suggestions.append("Run the same look through Guided Photo Capture for a higher-confidence read.")
        if avatar.body_profile.posture_hint == "single_photo":
            suggestions.append("Use front + side capture if you want stronger feedback about proportion and silhouette.")
        if not question:
            suggestions.append("Ask a direct question like 'is this flattering?' or 'would this work for dinner?'")
        if style_goal:
            suggestions.append(f"Ask for a version of this look that pushes harder toward '{style_goal}'.")
        return suggestions[:3]

    def _comparison_score(
        self,
        *,
        avatar: UserAvatar,
        garment: GarmentItem,
        commentary: StylistCommentary,
        request: TryOnRequest,
        occasion: str | None,
        style_goal: str | None,
    ) -> float:
        score = commentary.confidence_score * 0.52
        score += min(len(commentary.what_works) * 0.07, 0.21)
        score -= min(len(commentary.watch_outs) * 0.05, 0.15)
        score -= min(len(commentary.fit_caveats) * 0.04, 0.12)
        if request.camera_angle.value == "front":
            score += 0.04
        if avatar.body_profile.profile_confidence >= 0.6:
            score += 0.04
        if occasion:
            occ = occasion.lower()
            if occ in {"dinner", "date", "event"} and garment.category in {GarmentCategory.DRESS, GarmentCategory.OUTERWEAR, GarmentCategory.TOP}:
                score += 0.05
            if occ in {"work", "office"} and garment.category in {GarmentCategory.TOP, GarmentCategory.OUTERWEAR, GarmentCategory.BOTTOM}:
                score += 0.05
            if occ in {"travel", "casual"} and garment.category in {GarmentCategory.TOP, GarmentCategory.BOTTOM, GarmentCategory.FOOTWEAR}:
                score += 0.05
        if style_goal:
            goal = style_goal.lower()
            if goal in {"polished", "clean", "structured"} and garment.category in {GarmentCategory.OUTERWEAR, GarmentCategory.TOP}:
                score += 0.04
            if goal in {"easy", "casual", "relaxed"} and garment.category in {GarmentCategory.TOP, GarmentCategory.BOTTOM, GarmentCategory.FOOTWEAR}:
                score += 0.04
        return round(score, 3)

    def _comparison_verdict(
        self,
        *,
        winner_label: str,
        loser_label: str,
        occasion: str | None,
        style_goal: str | None,
        winner_commentary: StylistCommentary,
    ) -> str:
        if occasion:
            return f"{winner_label} is the stronger option for {occasion} based on the current preview signals. {loser_label} is still viable, but it carries more uncertainty or weaker styling cues."
        if style_goal:
            return f"{winner_label} aligns better with a {style_goal} goal in the current preview. {loser_label} is not wrong, but it is less convincing for that direction."
        if winner_commentary.confidence_score >= 0.65:
            return f"{winner_label} looks like the better pick overall from these two previews."
        return f"{winner_label} edges out {loser_label}, but this is still a medium-confidence comparison rather than a decisive one."

    @staticmethod
    def _pairing_confidence(*, avatar: UserAvatar, garment: GarmentItem) -> float:
        avatar_signal = min(avatar.body_profile.profile_confidence * 0.5, 0.38)
        garment_signal = min(garment.confidence_scores.get("candidate_selection", 0.0) * 0.35, 0.28)
        category_signal = 0.13 if garment.category != GarmentCategory.UNKNOWN else 0.04
        penalty = 0.06 if avatar.body_profile.posture_hint == "single_photo" else 0.0
        return round(max(0.3, min(avatar_signal + garment_signal + category_signal + 0.16 - penalty, 0.84)), 2)

    def _pairing_recommendations(
        self,
        *,
        avatar: UserAvatar,
        garment: GarmentItem,
        occasion: str,
        style_goal: str,
        weather_hint: str,
    ) -> list[PairingRecommendation]:
        palette = self._pairing_palette(garment.color, style_goal=style_goal, occasion=occasion)
        recommendations: list[PairingRecommendation] = []

        if garment.category == GarmentCategory.TOP:
            bottom_suggestion = "high-rise tailored trousers" if avatar.body_profile.leg_length_ratio < 0.47 else "straight-leg trousers or relaxed denim"
            if style_goal in {"polished", "clean", "structured"}:
                bottom_suggestion = "high-rise tailored trousers"
            recommendations.extend(
                [
                    PairingRecommendation(
                        role="anchor_bottom",
                        suggested_category=GarmentCategory.BOTTOM.value,
                        suggestion=bottom_suggestion,
                        colors=palette["base"],
                        rationale="A cleaner lower half stabilizes the silhouette and keeps the top as the focal point.",
                        priority="high",
                    ),
                    PairingRecommendation(
                        role="shoe",
                        suggested_category=GarmentCategory.FOOTWEAR.value,
                        suggestion="sleek loafers or low-profile boots" if occasion in {"work", "office", "dinner", "date"} else "minimal sneakers",
                        colors=palette["shoe"],
                        rationale="Simple footwear keeps the line of the outfit balanced without fighting the top.",
                        priority="high",
                    ),
                    PairingRecommendation(
                        role="layer",
                        suggested_category=GarmentCategory.OUTERWEAR.value,
                        suggestion="structured overshirt or light blazer" if weather_hint in {"cool", "cold", "rainy"} or occasion in {"work", "dinner"} else "unstructured outer layer",
                        colors=palette["layer"],
                        rationale="An outer layer is optional, but it helps polish the look when the occasion or weather asks for more structure.",
                        priority="medium",
                    ),
                ]
            )
        elif garment.category == GarmentCategory.BOTTOM:
            top_suggestion = "fitted knit or tucked-in tee" if avatar.body_profile.shoulder_scale >= 1.02 else "slim structured top"
            if style_goal in {"relaxed", "easy", "casual"}:
                top_suggestion = "easy tee or soft knit"
            recommendations.extend(
                [
                    PairingRecommendation(
                        role="top",
                        suggested_category=GarmentCategory.TOP.value,
                        suggestion=top_suggestion,
                        colors=palette["accent"],
                        rationale="A cleaner top keeps the lower half visually intentional instead of letting proportions drift.",
                        priority="high",
                    ),
                    PairingRecommendation(
                        role="shoe",
                        suggested_category=GarmentCategory.FOOTWEAR.value,
                        suggestion="low-profile sneakers" if occasion in {"casual", "travel"} else "ankle boots or loafers",
                        colors=palette["shoe"],
                        rationale="Footwear should match the formality of the bottom rather than overpower it.",
                        priority="high",
                    ),
                    PairingRecommendation(
                        role="layer",
                        suggested_category=GarmentCategory.OUTERWEAR.value,
                        suggestion="cropped jacket or clean coat",
                        colors=palette["layer"],
                        rationale="A controlled outer layer helps shape the upper body and complete the outfit formula.",
                        priority="medium",
                    ),
                ]
            )
        elif garment.category == GarmentCategory.DRESS:
            recommendations.extend(
                [
                    PairingRecommendation(
                        role="shoe",
                        suggested_category=GarmentCategory.FOOTWEAR.value,
                        suggestion="strappy heels or pointed flats" if occasion in {"dinner", "date", "event"} else "minimal sandals or sneakers",
                        colors=palette["shoe"],
                        rationale="Footwear is the fastest way to push a dress more polished or more casual.",
                        priority="high",
                    ),
                    PairingRecommendation(
                        role="layer",
                        suggested_category=GarmentCategory.OUTERWEAR.value,
                        suggestion="cropped jacket or clean long coat" if weather_hint in {"cool", "cold"} else "light cardigan or no layer",
                        colors=palette["layer"],
                        rationale="The right outer layer adds context without obscuring the dress silhouette.",
                        priority="high",
                    ),
                    PairingRecommendation(
                        role="accessory",
                        suggested_category=GarmentCategory.ACCESSORY.value,
                        suggestion="small structured bag and restrained jewelry",
                        colors=palette["accent"],
                        rationale="Accessories should sharpen the mood without adding too many competing signals.",
                        priority="medium",
                    ),
                ]
            )
        elif garment.category == GarmentCategory.OUTERWEAR:
            recommendations.extend(
                [
                    PairingRecommendation(
                        role="base_top",
                        suggested_category=GarmentCategory.TOP.value,
                        suggestion="clean fitted knit or plain tee",
                        colors=palette["base"],
                        rationale="A quiet base layer lets the outerwear carry the outfit instead of creating visual conflict.",
                        priority="high",
                    ),
                    PairingRecommendation(
                        role="bottom",
                        suggested_category=GarmentCategory.BOTTOM.value,
                        suggestion="straight trousers" if style_goal in {"polished", "structured"} else "clean denim or tailored trousers",
                        colors=palette["base"],
                        rationale="Balanced bottoms keep the outer layer feeling intentional rather than bulky.",
                        priority="high",
                    ),
                    PairingRecommendation(
                        role="shoe",
                        suggested_category=GarmentCategory.FOOTWEAR.value,
                        suggestion="boots or loafers" if occasion in {"work", "dinner", "date"} else "minimal sneakers",
                        colors=palette["shoe"],
                        rationale="Footwear should reinforce the structure of the outerwear, not pull the outfit casual by accident.",
                        priority="medium",
                    ),
                ]
            )
        elif garment.category == GarmentCategory.FOOTWEAR:
            recommendations.extend(
                [
                    PairingRecommendation(
                        role="top",
                        suggested_category=GarmentCategory.TOP.value,
                        suggestion="simple top with minimal detailing",
                        colors=palette["base"],
                        rationale="When shoes are the anchor, the rest of the look should stay clean enough to support them.",
                        priority="high",
                    ),
                    PairingRecommendation(
                        role="bottom",
                        suggested_category=GarmentCategory.BOTTOM.value,
                        suggestion="cropped trouser or straight jean",
                        colors=palette["base"],
                        rationale="A clean hem line helps footwear read clearly instead of getting lost.",
                        priority="high",
                    ),
                    PairingRecommendation(
                        role="layer",
                        suggested_category=GarmentCategory.OUTERWEAR.value,
                        suggestion="light outer layer matched to the shoe mood",
                        colors=palette["layer"],
                        rationale="The outer layer should echo the shoe energy without duplicating it.",
                        priority="medium",
                    ),
                ]
            )
        else:
            recommendations.extend(
                [
                    PairingRecommendation(
                        role="core_top",
                        suggested_category=GarmentCategory.TOP.value,
                        suggestion="clean fitted top",
                        colors=palette["base"],
                        rationale="A controlled core layer gives the look a stable foundation.",
                        priority="high",
                    ),
                    PairingRecommendation(
                        role="core_bottom",
                        suggested_category=GarmentCategory.BOTTOM.value,
                        suggestion="straight-leg trouser or clean denim",
                        colors=palette["base"],
                        rationale="A simple lower half makes the outfit easier to complete around the current piece.",
                        priority="high",
                    ),
                    PairingRecommendation(
                        role="shoe",
                        suggested_category=GarmentCategory.FOOTWEAR.value,
                        suggestion="minimal shoes that match the occasion",
                        colors=palette["shoe"],
                        rationale="Neutral footwear keeps the outfit flexible while the MVP still lacks wardrobe-specific grounding.",
                        priority="medium",
                    ),
                ]
            )
        return recommendations[:4]

    def _outfit_formula(
        self,
        *,
        garment: GarmentItem,
        recommendations: list[PairingRecommendation],
        occasion: str,
    ) -> list[str]:
        formula = [garment.category.value]
        formula.extend(rec.suggested_category for rec in recommendations[:3])
        if occasion:
            formula.append(f"occasion:{occasion}")
        return formula

    def _pairing_summary(
        self,
        *,
        garment: GarmentItem,
        recommendations: list[PairingRecommendation],
        occasion: str,
        style_goal: str,
    ) -> str:
        first = recommendations[0].suggestion if recommendations else "clean supporting pieces"
        if occasion and style_goal:
            return f"For {occasion}, I would build around this {garment.category.value} with {first} and keep the rest of the look pointed toward a {style_goal} finish."
        if occasion:
            return f"For {occasion}, this {garment.category.value} works best when the supporting pieces stay cleaner and more intentional."
        if style_goal:
            return f"To push this {garment.category.value} toward a {style_goal} direction, keep the companion pieces controlled and let this item lead."
        return f"I would treat this {garment.category.value} as the anchor and pair it with simple supporting pieces so the outfit reads coherent rather than busy."

    def _pairing_palette(self, color: str | None, *, style_goal: str, occasion: str) -> dict[str, list[str]]:
        normalized = (color or "").strip().lower()
        if normalized in {"black", "white", "cream", "beige", "camel", "gray", "grey", "navy", "brown"}:
            base = [normalized] if normalized else ["black", "cream"]
            accent = ["silver", "deep green"] if occasion in {"dinner", "date", "event"} else ["white", "tan"]
            shoe = ["black", "brown"]
            layer = ["camel", "navy", "charcoal"]
        elif normalized in {"red", "burgundy", "green", "blue", "pink", "yellow", "orange", "purple"}:
            base = ["black", "white", "cream", "charcoal"]
            accent = [normalized, "metallic"] if style_goal in {"polished", "structured"} else [normalized, "tan"]
            shoe = ["black", "tan"]
            layer = ["cream", "charcoal", "navy"]
        else:
            base = ["black", "white", "cream"]
            accent = ["silver", "tan"] if style_goal in {"polished", "clean"} else ["olive", "brown"]
            shoe = ["black", "tan"]
            layer = ["charcoal", "camel"]
        return {
            "base": base[:2],
            "accent": accent[:2],
            "shoe": shoe[:2],
            "layer": layer[:2],
        }

    def _decision_factors(
        self,
        *,
        primary_commentary: StylistCommentary,
        secondary_commentary: StylistCommentary,
        primary_garment: GarmentItem,
        secondary_garment: GarmentItem,
        occasion: str | None,
        style_goal: str | None,
    ) -> list[str]:
        factors: list[str] = []
        if primary_commentary.confidence_score != secondary_commentary.confidence_score:
            stronger = "Look A" if primary_commentary.confidence_score > secondary_commentary.confidence_score else "Look B"
            factors.append(f"{stronger} has the stronger overall preview confidence signal.")
        if len(primary_commentary.watch_outs) != len(secondary_commentary.watch_outs):
            safer = "Look A" if len(primary_commentary.watch_outs) < len(secondary_commentary.watch_outs) else "Look B"
            factors.append(f"{safer} has fewer immediate watch-outs in the current renderer.")
        if primary_garment.category != secondary_garment.category:
            factors.append(f"You are comparing different garment categories ({primary_garment.category.value} vs {secondary_garment.category.value}), so the choice depends more on styling intent than pure fit.")
        if occasion:
            factors.append(f"The comparison is being weighted toward what works for {occasion}.")
        if style_goal:
            factors.append(f"The comparison is also biased toward a {style_goal} style goal.")
        return factors[:4]
