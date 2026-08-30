"""
diagnosis_engine
-----------------
Stage 2 of the two-stage architecture: "Diagnose This Field".

Stage 1 (unchanged -- field_analyzer.py + risk_engine.py) answers "is
something unusual happening in this field?" from Sentinel-2 + weather +
IoT alone, without trying to name an exact pest/disease.

This module answers the next question, once the farmer opts in to a
diagnosis: given everything Stage 1 already knows about the field PLUS
whatever ground evidence the farmer adds, what's a reasonable,
confidence-aware hypothesis -- and what should the farmer verify before
treating anything?

Two responsibilities, matching the two calls the diagnosis routes make:

  build_diagnosis_context(field)
      Read-only. Assembles the "why are we asking you to inspect this
      field" screen from data that already exists: the field's latest
      Analysis (zone stats, priority/stressed zones), latest
      RiskAssessment (causes, confidence), latest weather, latest sensor
      reading. The farmer never re-types any of this.

  run_diagnosis(diagnosis)
      Takes a persisted FieldDiagnosis (with its frozen context_snapshot
      and any uploaded DiagnosisEvidence rows) and produces a
      possible_cause / confidence / supporting_evidence / recommended_*
      result.

IMPORTANT -- NOT AN IMAGE CLASSIFIER: this project does not include a
trained model that looks at pixels and names a pest. Per the product
brief ("Do not fake ML predictions as trained predictions"), this engine
never invents a visual diagnosis from an uploaded photo. What it DOES do
is combine two things that are both real and already explainable:

  1. The existing rule-based RiskAssessment causes (vegetation stress,
     NDVI trend, weather/soil signals) -- Stage 1's own reasoning.
  2. The farmer's OWN observation, captured as a simple structured tag
     when they upload a photo (what kind of damage they see: chewing,
     sucking, curling, wilting, leaf spot, discoloration, or "not sure").
     That tag is the farmer's label, not an AI classification of the
     image -- the engine is reasoning from a fact the farmer supplied,
     the same way it reasons from a weather reading.

Confidence is capped at "medium" by this engine on purpose: a rule-based
combination of a stress signal and a farmer-reported damage pattern is
corroborating evidence, not a lab identification. Confidence only reaches
"high" when the field is already showing decisively high, well-agreeing
risk *and* the farmer has both supplied a damage-pattern tag and
uploaded photographic evidence for a priority zone -- and even then the
recommendation language stays "verify before treating", never "this is
definitely X" (see recommended_verification, always populated).
"""

from __future__ import annotations

from typing import Dict, List, Optional

from backend.config import Config
from backend.diagnosis_service import analyze_diagnosis_image

# Farmer-selected damage pattern -> plain-language hypothesis + what to
# verify on the ground. Deliberately broad categories (never a specific
# pest/disease species) so this reads as "narrowing the search", not an
# identification.
DAMAGE_PATTERN_HINTS = {
    "chewing": {
        "label": "Chewing-pest activity",
        "hypothesis": "possible chewing-pest activity (e.g. caterpillar, beetle, or grasshopper feeding)",
        "verify": "Inspect the underside of leaves, leaf margins, and the whorl/growing point for the insect itself or frass.",
    },
    "sucking": {
        "label": "Sucking-pest activity",
        "hypothesis": "possible sucking-pest activity (e.g. aphids, thrips, or whiteflies)",
        "verify": "Check the underside of young leaves and shoot tips for clusters of small insects or sticky honeydew.",
    },
    "curling": {
        "label": "Leaf curling",
        "hypothesis": "possible sucking-pest activity or an early viral infection",
        "verify": "Unroll a curled leaf and check for insects inside, and compare curled vs. healthy leaves for mottling.",
    },
    "wilting": {
        "label": "Wilting",
        "hypothesis": "possible water stress, root/stem damage, or a vascular disease",
        "verify": "Check soil moisture at the root zone and look for stem discoloration or lesions at the base of the plant.",
    },
    "leaf_spot": {
        "label": "Leaf spotting",
        "hypothesis": "possible fungal or bacterial leaf disease",
        "verify": "Note the spot color, shape and whether it spreads with a yellow halo, and check if it worsens after rain/humidity.",
    },
    "discoloration": {
        "label": "Discoloration",
        "hypothesis": "possible nutrient deficiency or environmental stress",
        "verify": "Note which leaves are affected (old vs. new growth) and the discoloration pattern (uniform, veins, margins).",
    },
    "not_sure": {
        "label": "Unclear / not sure",
        "hypothesis": None,
        "verify": "A closer, well-lit photo of the affected area and the leaf underside would help narrow this down.",
    },
}

# Plain-language phrasing for RiskAssessment cause factors (see
# risk_engine.py) when no farmer damage-pattern tag is available to lead
# with. 'hyperspectral_confirmed' is deliberately excluded: it is a
# corroboration signal ("a second sensor agrees"), not a cause in its own
# right, so it should never be the headline hypothesis.
RISK_FACTOR_PHRASES = {
    "vegetation_stress": "vegetation stress",
    "declining_trend": "a declining vegetation-health trend",
    "water_deficit": "water deficit / drought stress",
    "waterlogging_risk": "waterlogging",
    "heat_stress": "heat stress",
    "cold_stress": "cold stress",
    "soil_ph_out_of_range": "a soil pH imbalance",
}

IMAGE_TYPE_LABELS = {
    "leaf": "crop/leaf photo",
    "pest_insect": "pest/insect photo",
    "closeup": "close-up of the affected area",
    "beneficial_insect": "possible beneficial-insect photo",
    "other": "farmer-provided photo",
}


def _num(value, default=None):
    return value if isinstance(value, (int, float)) else default


def build_diagnosis_context(field) -> Dict:
    """Assembles the read-only context payload shown on 'Diagnose This
    Field' before the farmer picks an inspection method. Pulls only from
    data the existing Stage 1 pipeline already produced -- no new
    fetches, no re-analysis."""

    analysis = field.latest_analysis()
    risk = field.latest_risk_assessment()
    weather = field.latest_weather()
    sensor = field.latest_sensor_reading()

    analysis_dict = analysis.to_dict(include_zones=True) if analysis else None
    risk_dict = risk.to_dict() if risk else None

    priority_zones = []
    if analysis_dict:
        zones = analysis_dict.get("zones") or []
        stressed = [z for z in zones if z.get("health_status") == "stressed"]
        moderate = [z for z in zones if z.get("health_status") == "moderate"]
        # Worst NDVI first within each bucket.
        stressed.sort(key=lambda z: _num(z.get("ndvi"), 1.0))
        moderate.sort(key=lambda z: _num(z.get("ndvi"), 1.0))
        ranked = stressed + moderate
        limit = Config.DIAGNOSIS_MAX_PRIORITY_ZONES
        priority_zones = [
            {
                "zone_id": z["zone_id"],
                "health_status": z["health_status"],
                "ndvi": z.get("ndvi"),
                "area_ha": z.get("area_ha"),
            }
            for z in ranked[:limit]
        ]

    zone_stats = (analysis_dict or {}).get("zone_stats") or {}
    total_zones = zone_stats.get("total") or 0
    stressed_count = zone_stats.get("stressed") or 0

    return {
        "field": {
            "id": field.id,
            "name": field.name,
            "crop_type": field.crop_type,
            "crop_stage": field.crop_stage,
            "area_ha": field.area_ha,
        },
        "analysis": analysis_dict,
        "risk": risk_dict,
        "weather": weather.to_dict() if weather else None,
        "sensor": sensor.to_dict() if sensor else None,
        "priority_zones": priority_zones,
        "zone_summary": {
            "stressed": stressed_count,
            "total": total_zones,
        },
        "has_sufficient_context": bool(analysis_dict and risk_dict),
    }


def _confidence_label(score: float) -> str:
    if score >= 0.65:
        return "high"
    if score >= 0.35:
        return "medium"
    return "low"

# def build_intervention_simulator(
#     risk_level,
#     pest_detections,
#     disease_detections,
#     beneficial_flagged,
# ):
#     pest_confirmed = bool(pest_detections)
#     disease_confirmed = bool(disease_detections)

#     if beneficial_flagged:
#         return [
#             {
#                 "option": "Do nothing",
#                 "expected_result": "Risk may continue if the pest is active",
#                 "cost": "₹0",
#                 "chemical_impact": "None",
#                 "recommendation": "Monitor closely",
#             },
#             {
#                 "option": "Bio-control treatment",
#                 "expected_result": "Potential protection with low chemical impact",
#                 "cost": "Low",
#                 "chemical_impact": "Low",
#                 "recommendation": "Preferred if pest pressure is confirmed",
#             },
#             {
#                 "option": "Targeted chemical spray",
#                 "expected_result": "Potentially high pest suppression",
#                 "cost": "Medium",
#                 "chemical_impact": "Medium",
#                 "recommendation": "Avoid unless necessary",
#             },
#             {
#                 "option": "Whole-field spray",
#                 "expected_result": "Potentially high suppression",
#                 "cost": "High",
#                 "chemical_impact": "High",
#                 "recommendation": "Avoid",
#             },
#         ]

#     if risk_level == "low":
#         return [
#             {
#                 "option": "Do nothing",
#                 "expected_result": "Risk of spread: low",
#                 "cost": "₹0",
#                 "chemical_impact": "None",
#                 "recommendation": "Recommended while monitoring",
#             },
#             {
#                 "option": "Bio-control treatment",
#                 "expected_result": "May provide protection if pest pressure increases",
#                 "cost": "Low",
#                 "chemical_impact": "Low",
#                 "recommendation": "Optional if symptoms increase",
#             },
#             {
#                 "option": "Targeted chemical spray",
#                 "expected_result": "Potentially high protection",
#                 "cost": "Medium",
#                 "chemical_impact": "Medium",
#                 "recommendation": "Not recommended at current risk",
#             },
#             {
#                 "option": "Whole-field spray",
#                 "expected_result": "Potentially high protection",
#                 "cost": "High",
#                 "chemical_impact": "High",
#                 "recommendation": "Avoid",
#             },
#         ]

#     if risk_level == "moderate":
#         return [
#             {
#                 "option": "Do nothing",
#                 "expected_result": "Risk of spread: moderate",
#                 "cost": "₹0",
#                 "chemical_impact": "None",
#                 "recommendation": "Not preferred",
#             },
#             {
#                 "option": "Bio-control treatment",
#                 "expected_result": "Moderate-to-high protection",
#                 "cost": "Low",
#                 "chemical_impact": "Low",
#                 "recommendation": "Best early-stage option",
#             },
#             {
#                 "option": "Targeted chemical spray",
#                 "expected_result": "High protection in affected zones",
#                 "cost": "Medium",
#                 "chemical_impact": "Medium",
#                 "recommendation": "Use only in confirmed affected zones",
#             },
#             {
#                 "option": "Whole-field spray",
#                 "expected_result": "High protection",
#                 "cost": "High",
#                 "chemical_impact": "High",
#                 "recommendation": "Avoid",
#             },
#         ]

#     return [
#         {
#             "option": "Do nothing",
#             "expected_result": "Risk of spread: high",
#             "cost": "₹0",
#             "chemical_impact": "None",
#             "recommendation": "Not advised",
#         },
#         {
#             "option": "Bio-control treatment",
#             "expected_result": "Moderate-to-high protection",
#             "cost": "Low",
#             "chemical_impact": "Low",
#             "recommendation": "Preferred where appropriate",
#         },
#         {
#             "option": "Targeted chemical spray",
#             "expected_result": "High protection in affected zones",
#             "cost": "Medium",
#             "chemical_impact": "Medium",
#             "recommendation": "Use only in confirmed high-risk zones",
#         },
#         {
#             "option": "Whole-field spray",
#             "expected_result": "High protection",
#             "cost": "High",
#             "chemical_impact": "High",
#             "recommendation": "Consider only for severe field-wide outbreak",
#         },
#     ]

def run_diagnosis(diagnosis) -> Dict:
    ctx = diagnosis.context_snapshot or {}
    risk = ctx.get("risk") or {}
    causes = risk.get("causes") or []
    weather = ctx.get("weather") or {}
    evidence = list(diagnosis.evidence_items or [])

    supporting: List[str] = []
    verification: List[str] = []
    intervention: List[str] = []

    risk_level = (risk.get("risk_level") or "low").lower()
    risk_conf = _num(risk.get("confidence"), 0.0) or 0.0

    real_causes = [
        c for c in causes
        if c.get("factor") in RISK_FACTOR_PHRASES
    ]

    vegetation_cause = next(
        (c for c in causes if c.get("factor") == "vegetation_stress"),
        None,
    )

    gemini_results = []
    pest_detections = []
    disease_detections = []
    beneficial_flagged = False
    damage_hints_seen = []

    for item in evidence:
        img_label = IMAGE_TYPE_LABELS.get(
            item.image_type,
            "farmer-provided photo"
        )

        zone_label = (
            f"Zone {item.zone_id}"
            if item.zone_id is not None
            else "the field"
        )

        if item.image_type == "beneficial_insect":
            beneficial_flagged = True

        if item.damage_pattern in DAMAGE_PATTERN_HINTS:
            damage_hints_seen.append(
                DAMAGE_PATTERN_HINTS[item.damage_pattern]
            )

        supporting.append(
            f"Farmer uploaded a {img_label} from {zone_label}."
        )

        if item.damage_pattern in DAMAGE_PATTERN_HINTS:
            supporting.append(
                f"Farmer-observed pattern: "
                f"{DAMAGE_PATTERN_HINTS[item.damage_pattern]['label'].lower()}."
            )

        if item.note:
            supporting.append(
                f"Farmer note: {item.note.strip()}"
            )

        try:
            visual_result = analyze_diagnosis_image(item)
            gemini_results.append(visual_result)

            pest_detection = visual_result.get("pest_detection") or {}
            if pest_detection.get("detected"):
                pest_detections.append(pest_detection)

            disease_detection = visual_result.get("disease_detection") or {}
            if disease_detection.get("detected"):
                disease_detections.append(disease_detection)

            observation = visual_result.get("visual_observation")
            if observation:
                supporting.append(
                    f"Image analysis: {observation}"
                )

            possible_causes = visual_result.get("possible_causes") or []

            for cause in possible_causes[:3]:
                supporting.append(
                    f"Image-based possible cause: {cause}"
                )

            image_pattern = visual_result.get("damage_pattern")

            if (
                image_pattern in DAMAGE_PATTERN_HINTS
                and image_pattern != "not_sure"
            ):
                hint = DAMAGE_PATTERN_HINTS[image_pattern]

                if hint not in damage_hints_seen:
                    damage_hints_seen.append(hint)

            image_verification = visual_result.get("verification") or []

            for item_verification in image_verification[:3]:
                if item_verification not in verification:
                    verification.append(item_verification)

        except Exception as exc:
            supporting.append(
                f"Image analysis could not be completed for {zone_label}: {exc}"
            )

    if causes:
        for cause in causes[:3]:
            detail = cause.get("detail")
            if detail:
                supporting.append(detail)

    if not causes:
        supporting.append(
            "No significant satellite, weather, or IoT stress signal was detected."
        )

    named_hints = [
        hint for hint in damage_hints_seen
        if hint.get("hypothesis")
    ]

    gemini_causes = []

    for result in gemini_results:
        for cause in result.get("possible_causes") or []:
            if cause and cause not in gemini_causes:
                gemini_causes.append(cause)

    if pest_detections:
                for pest in pest_detections:
                    pest_name = pest.get("name") or "possible pest"
                    pest_confidence = pest.get("confidence")

                    if isinstance(pest_confidence, (int, float)):
                        pest_confidence_text = f"{pest_confidence:.0%} visual confidence"
                    else:
                        pest_confidence_text = "visual assessment"

                    supporting.append(
                        f"Possible pest detected from image: "
                        f"{pest_name} ({pest_confidence_text})."
                    )

    if disease_detections:
                for disease in disease_detections:
                    disease_name = disease.get("name") or "possible disease"
                    disease_confidence = disease.get("confidence")

                    if isinstance(disease_confidence, (int, float)):
                        disease_confidence_text = f"{disease_confidence:.0%} visual confidence"
                    else:
                        disease_confidence_text = "visual assessment"

                    supporting.append(
                        f"Possible disease detected from image: "
                        f"{disease_name} ({disease_confidence_text})."
                    )

    if gemini_causes:
        possible_cause = gemini_causes[0]

        if len(gemini_causes) > 1:
            possible_cause += (
                f"; other possible cause: {gemini_causes[1]}"
            )

        if real_causes:
            possible_cause += (
                ", with field-level stress indicators providing supporting context"
            )

    elif beneficial_flagged:
        possible_cause = (
            "Possible beneficial/natural-enemy insect present — "
            "crop stress cause remains unconfirmed"
        )

    elif named_hints:
        possible_cause = named_hints[0]["hypothesis"].capitalize()

        if real_causes:
            possible_cause += (
                ", supported by the field stress indicators"
            )

    elif real_causes:
        leading = real_causes[0]

        factor_phrase = RISK_FACTOR_PHRASES.get(
            leading.get("factor"),
            "crop stress"
        )

        possible_cause = (
            f"Possible {factor_phrase} — "
            "exact cause not yet confirmed"
        )

    else:
        possible_cause = (
            "Insufficient visual and field evidence to identify "
            "a likely cause"
        )

    if vegetation_cause:
        detail = vegetation_cause.get("detail")

        if detail and detail not in supporting:
            supporting.insert(0, detail)

    gemini_confidences = []

    for result in gemini_results:
        confidence = result.get("confidence")

        if isinstance(confidence, (int, float)):
            gemini_confidences.append(
                max(0.0, min(float(confidence), 1.0))
            )

    if gemini_confidences:
        image_confidence = sum(gemini_confidences) / len(
            gemini_confidences
        )

        combined_score = (
            risk_conf * 0.55 +
            image_confidence * 0.45
        )

        confidence_score = min(
            max(combined_score, 0.05),
            0.85
        )
    else:
        confidence_score = min(
            max(risk_conf, 0.05),
            0.85
        )

    if confidence_score >= 0.65:
        confidence_level = "high"
    elif confidence_score >= 0.35:
        confidence_level = "medium"
    else:
        confidence_level = "low"

    if risk_level == "low" and not gemini_results:
        confidence_level = "low"

    if risk_level in ("moderate", "high", "critical"):
        verification.append(
            "Inspect the priority zones in person before applying any treatment."
        )

    if not evidence and risk_level in ("moderate", "high", "critical"):
        verification.append(
            "Upload a clear photo of the affected leaves, plant, or pest "
            "to provide additional ground evidence."
        )

    if not gemini_results and evidence:
        verification.append(
            "Repeat the image upload with a clear, well-lit photograph "
            "of the affected plant or leaf."
        )

    if risk_level == "low":
        intervention.append(
            "No immediate treatment is recommended. Continue monitoring "
            "the field and re-check the affected zones during the next analysis."
        )

    elif beneficial_flagged:
        intervention.append(
            "Avoid unnecessary broad-spectrum pesticide use until the "
            "beneficial insect and crop symptoms are confirmed."
        )

    elif gemini_results:
        intervention.append(
            "Use the image-based result as a preliminary hypothesis only. "
            "Confirm the suspected pest, disease, or stress condition "
            "on affected plants before treatment."
        )

    elif named_hints:
        intervention.append(
            "Do not apply a pesticide solely from this diagnosis. "
            "Confirm the suspected pest or disease on the affected plants first."
        )

    elif risk_level in ("high", "critical"):
        intervention.append(
            "Do not automatically spray. Confirm the cause in the affected "
            "zones first, then select a targeted intervention appropriate "
            "to the confirmed problem."
        )

    else:
        intervention.append(
            "Monitor the affected zones and collect additional ground "
            "evidence before deciding on treatment."
        )

    if risk_level in ("moderate", "high", "critical"):
        intervention.append(
            "Re-analyze the field after the next monitoring interval to "
            "check whether the stressed area is increasing or recovering."
        )

    protection_alert = None

    if beneficial_flagged:
        protection_alert = (
            "Possible beneficial insect reported. Avoid unnecessary "
            "broad-spectrum pesticide spraying until the observation "
            "is verified."
        )

    if not verification:
        verification.append(
            "Continue routine field monitoring and collect photographic "
            "evidence if symptoms become visible."
        )

    # intervention_simulator = build_intervention_simulator(
    #     risk_level=risk_level,
    #     pest_detections=pest_detections,
    #     disease_detections=disease_detections,
    #     beneficial_flagged=beneficial_flagged,
    # )

    return {
        "possible_cause": possible_cause,
        "confidence_level": confidence_level,
        "confidence_score": round(confidence_score, 3),
        "supporting_evidence": supporting,
        "recommended_verification": verification,
        "recommended_intervention": intervention,
        # "intervention_simulator": intervention_simulator,
        "pest_detection": pest_detections,
        "disease_detection": disease_detections,
        "protection_alert": protection_alert,
    }