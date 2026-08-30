"""
risk_engine
-----------
Turns one FeatureSnapshot dict (see feature_engineering.py) into a stress
/ risk assessment: a risk level + score, a confidence value, a ranked list
of likely causes, and farmer-facing recommendations.

THIS IS DELIBERATELY NOT ML. Per the brief, the priority right now is a
correct, explainable foundation -- data collection, storage, and feature
preparation -- not a model. So this module is a small, transparent,
weighted rule engine:

  risk_score      = clipped sum of independently-reasoned "risk components"
  confidence      = data-completeness + cross-source-agreement heuristic
  causes          = the components that actually fired, ranked by weight
  recommendations = a fixed mapping from cause -> actionable farmer advice

It is written as a single pure function, `assess_risk(features) -> dict`,
that only reads from the feature dict and returns plain data. That
boundary is intentional: a future ML model can be dropped in as a second
implementation of the exact same function signature (features in, the
same {risk_level, risk_score, confidence, causes, recommendations} shape
out) and every caller (routes/fields.py, the UI) keeps working unchanged.
Config.RISK_ENGINE_METHOD ('rule_based_v1' today) is what actually gets
stamped onto each stored RiskAssessment row, so swapping engines later is
visible in the data without a schema change.
"""

from __future__ import annotations

from typing import Dict, List

from backend.config import Config

# --- thresholds (kept local to this module -- they are this engine's own
# tuning knobs, not deployment config) -----------------------------------
STRESSED_AREA_HIGH = 0.35
STRESSED_AREA_MODERATE = 0.10
NDVI_DECLINE_SIGNIFICANT = -0.05
NDVI_DECLINE_MILD = -0.02
LOW_SOIL_MOISTURE = 20.0
VERY_LOW_SOIL_MOISTURE = 12.0
LOW_NDMI = 0.2
DRY_STREAK_DAYS = 3
HIGH_PRECIP_TOTAL_MM = 40.0
VERY_HIGH_SOIL_MOISTURE = 85.0
PH_LOW = 5.5
PH_HIGH = 8.0


def assess_risk(features: Dict) -> Dict:
    causes: List[Dict] = []
    score = 0.0

    # ---------------------------------------------------- vegetation ------
    stressed_frac = features.get("sat_stressed_area_fraction")
    moderate_frac = features.get("sat_moderate_area_fraction")
    if stressed_frac is not None:
        if stressed_frac >= STRESSED_AREA_HIGH:
            w = 0.35
            causes.append({
                "factor": "vegetation_stress",
                "detail": f"{stressed_frac * 100:.0f}% of the field's area is classified stressed by NDVI/NDRE/SAVI/NDMI.",
                "weight": w,
            })
            score += w
        elif stressed_frac >= STRESSED_AREA_MODERATE:
            w = 0.18
            causes.append({
                "factor": "vegetation_stress",
                "detail": f"{stressed_frac * 100:.0f}% of the field's area is classified stressed.",
                "weight": w,
            })
            score += w
        elif moderate_frac and moderate_frac >= 0.3:
            w = 0.08
            if moderate_frac >= 0.70:
                w = 0.25
            causes.append({
                "factor": "vegetation_stress",
                "detail": f"{moderate_frac * 100:.0f}% of the field shows moderate (borderline) vegetation health.",
                "weight": w,
            })
            score += w

    # ------------------------------------------------------- ndvi trend ---
    ndvi_delta = features.get("hist_ndvi_delta")
    if ndvi_delta is not None:
        if ndvi_delta <= NDVI_DECLINE_SIGNIFICANT:
            w = 0.22
            causes.append({
                "factor": "declining_trend",
                "detail": f"Mean NDVI dropped by {abs(ndvi_delta):.3f} since the previous analysis -- an emerging (not yet visually obvious) stress signal.",
                "weight": w,
            })
            score += w
        elif ndvi_delta <= NDVI_DECLINE_MILD:
            w = 0.1
            causes.append({
                "factor": "declining_trend",
                "detail": f"Mean NDVI eased down by {abs(ndvi_delta):.3f} since the previous analysis.",
                "weight": w,
            })
            score += w

    # --------------------------------------------------- moisture deficit -
    soil_moisture = features.get("iot_avg_soil_moisture_pct")
    ndmi = features.get("sat_mean_ndmi")
    dry_streak = features.get("wx_dry_reading_streak") or 0
    moisture_signals = 0
    moisture_detail_bits = []
    if soil_moisture is not None and soil_moisture < LOW_SOIL_MOISTURE:
        moisture_signals += 2 if soil_moisture < VERY_LOW_SOIL_MOISTURE else 1
        moisture_detail_bits.append(f"soil moisture averaging {soil_moisture:.0f}%")
    if ndmi is not None and ndmi < LOW_NDMI:
        moisture_signals += 1
        moisture_detail_bits.append(f"satellite moisture index (NDMI) at {ndmi:.2f}")
    if dry_streak >= DRY_STREAK_DAYS:
        moisture_signals += 1
        moisture_detail_bits.append(f"{dry_streak} consecutive dry weather readings")
    if moisture_signals >= 2:
        w = 0.28 if moisture_signals >= 3 else 0.16
        causes.append({
            "factor": "water_deficit",
            "detail": "Multiple sources agree on water stress: " + ", ".join(moisture_detail_bits) + ".",
            "weight": w,
        })
        score += w
    elif moisture_signals == 1:
        w = 0.08
        causes.append({
            "factor": "water_deficit",
            "detail": "Possible water stress: " + ", ".join(moisture_detail_bits) + " (single-source signal).",
            "weight": w,
        })
        score += w

    # ---------------------------------------------------- waterlogging ----
    total_precip = features.get("wx_total_precip_mm")
    if (
        total_precip is not None
        and total_precip >= HIGH_PRECIP_TOTAL_MM
        and soil_moisture is not None
        and soil_moisture >= VERY_HIGH_SOIL_MOISTURE
    ):
        w = 0.15
        causes.append({
            "factor": "waterlogging_risk",
            "detail": f"{total_precip:.0f}mm of recent rainfall combined with saturated soil ({soil_moisture:.0f}% moisture) raises waterlogging/fungal disease risk.",
            "weight": w,
        })
        score += w

    # --------------------------------------------------- heat / cold ------
    heat_readings = features.get("wx_heat_stress_readings") or 0
    cold_readings = features.get("wx_cold_stress_readings") or 0
    if heat_readings > 0:
        w = min(0.05 * heat_readings, 0.15)
        causes.append({
            "factor": "heat_stress",
            "detail": f"{heat_readings} recent weather reading(s) at or above 38°C.",
            "weight": round(w, 3),
        })
        score += w
    if cold_readings > 0:
        w = min(0.05 * cold_readings, 0.15)
        causes.append({
            "factor": "cold_stress",
            "detail": f"{cold_readings} recent weather reading(s) at or below 5°C.",
            "weight": round(w, 3),
        })
        score += w

    # ------------------------------------------------------------ soil ----
    soil_ph = features.get("iot_avg_soil_ph")
    if soil_ph is not None and (soil_ph < PH_LOW or soil_ph > PH_HIGH):
        w = 0.08
        causes.append({
            "factor": "soil_ph_out_of_range",
            "detail": f"Average soil pH of {soil_ph:.1f} is outside the typical 5.5-8.0 range for most field crops.",
            "weight": w,
        })
        score += w

    # ------------------------------------------------- hyperspectral -----
    checked = features.get("sat_hyperspectral_zones_checked") or 0
    confirmed = features.get("sat_hyperspectral_zones_confirmed_stress") or 0

    risk_score = max(0.0, min(score, 1.0))
    risk_level = _score_to_level(risk_score)
    causes.sort(key=lambda c: c["weight"], reverse=True)

    confidence = _estimate_confidence(features, causes)
    recommendations = _build_recommendations(causes, features, risk_level)

    return {
        "risk_level": risk_level,
        "risk_score": round(risk_score, 3),
        "confidence": round(confidence, 3),
        "causes": causes,
        "recommendations": recommendations,
        "method": Config.RISK_ENGINE_METHOD,
    }


def _score_to_level(score: float) -> str:
    if score >= 0.75:
        return "critical"
    if score >= 0.5:
        return "high"
    if score >= 0.25:
        return "moderate"
    return "low"


def _estimate_confidence(features: Dict, causes: List[Dict]) -> float:
    """Confidence reflects how much data backed the assessment, not how
    severe it is -- a 'low risk' call made with almost no data should show
    LOW confidence, not high, so the farmer knows to treat it as
    provisional rather than a clean bill of health."""
    sources = features.get("meta_sources_available") or {}
    confidence = 0.3

    if sources.get("satellite"):
        confidence += 0.2
    if sources.get("weather"):
        confidence += 0.15
    if sources.get("iot"):
        confidence += 0.1
        if features.get("iot_has_real_device_data"):
            confidence += 0.1  # real hardware > simulated fallback
    if sources.get("history"):
        confidence += 0.15

    # Cross-source agreement: multiple independent causes pointing the
    # same direction is more trustworthy than a single flagged signal.
    distinct_factors = {c["factor"] for c in causes}
    if len(distinct_factors) >= 2:
        confidence += 0.05
    if any(c["factor"] == "water_deficit" and "Multiple sources" in c["detail"] for c in causes):
        confidence += 0.05

    return max(0.05, min(confidence, 0.98))


def _build_recommendations(causes: List[Dict], features: Dict, risk_level: str) -> List[Dict]:
    recs: List[Dict] = []
    factors_present = {c["factor"] for c in causes}

    if "vegetation_stress" in factors_present or "declining_trend" in factors_present:
        recs.append({
            "title": "Scout the flagged zones in person",
            "detail": "Walk the stressed/moderate zones shown on the map to check for pests, disease, or nutrient symptoms the imagery alone can't distinguish.",
            "priority": "high" if risk_level in ("high", "critical") else "medium",
        })
    if "declining_trend" in factors_present:
        recs.append({
            "title": "Re-analyze sooner than usual",
            "detail": "Vegetation health is trending down. Run another Analyze in 5-7 days instead of waiting for the usual interval to confirm whether this is a real trend.",
            "priority": "medium",
        })
    if "water_deficit" in factors_present:
        recs.append({
            "title": "Plan irrigation for the affected zones",
            "detail": "Soil/satellite moisture signals point to a water deficit. Prioritize irrigation in the stressed zones over the rest of the field.",
            "priority": "high" if risk_level in ("high", "critical") else "medium",
        })
    if "waterlogging_risk" in factors_present:
        recs.append({
            "title": "Check drainage and watch for fungal disease",
            "detail": "Soil is saturated after heavy rain. Inspect drainage channels and monitor for early fungal/rot symptoms over the next few days.",
            "priority": "medium",
        })
    if "heat_stress" in factors_present:
        recs.append({
            "title": "Adjust for heat stress",
            "detail": "Recent high temperatures were recorded. Consider early-morning/evening irrigation timing and shading for heat-sensitive growth stages.",
            "priority": "medium",
        })
    if "cold_stress" in factors_present:
        recs.append({
            "title": "Guard against cold/frost damage",
            "detail": "Recent low temperatures were recorded. Protect young or flowering plants if frost is forecast.",
            "priority": "medium",
        })
    if "soil_ph_out_of_range" in factors_present:
        recs.append({
            "title": "Test and amend soil pH",
            "detail": "Average soil pH is outside the typical range for most field crops, which can limit nutrient uptake even when water/light are adequate.",
            "priority": "low",
        })

    if not recs:
        recs.append({
            "title": "Continue routine monitoring",
            "detail": "No significant stress signals detected in the current data. Keep the normal analysis and weather-monitoring cadence.",
            "priority": "low",
        })

    sources = features.get("meta_sources_available") or {}
    if not sources.get("iot") or not features.get("iot_has_real_device_data"):
        recs.append({
            "title": "Add real IoT sensors for higher-confidence readings",
            "detail": "This assessment is currently using simulated or no sensor data. Deploying real soil-moisture/temperature sensors will sharpen future risk and confidence scores.",
            "priority": "low",
        })

    return recs