"""
intervention_engine
---------------------
Stage 3 of the field-intelligence pipeline:

    Satellite Analysis -> Risk Engine -> Diagnosis Engine -> [this module]
    -> Approved-use pesticide dataset -> field-specific intervention options
    -> Intervention Simulator

THIS IS NOT AN ML MODEL and it is NOT a second diagnosis system. It
consumes the outputs the existing engines already produced
(RiskAssessment.causes, FieldDiagnosis.possible_cause /
confidence_level / evidence damage_pattern tags) and applies a small,
explainable set of rules to decide:

  1. whether a pesticide recommendation is even appropriate yet
     (see MONITOR / VERIFY / TARGETED / FIELD_WIDE below), and
  2. if it is, which rows of the approved-use dataset
     (backend/models.py::PesticideUse, queried only through
     backend/services/pesticide_data_service.py) match the crop and the
     suspected pest.

Per the project brief, this module never:
  - claims a pesticide "will cure" anything (language stays "approved-use
    records matching the crop/suspected pest", "verify before treating"),
  - invents a dosage or an approval that isn't a real PesticideUse row,
  - converts "vegetation stress" directly into "pest detected" -- a
    pesticide match is only surfaced once the Diagnosis Engine has
    produced a pest-plausible hypothesis (see PEST_PLAUSIBLE_PATTERNS),
  - recommends whole-field spraying by default -- FIELD_WIDE is only
    offered as an *additional option* alongside TARGETED, and only when
    the stressed-zone fraction and risk level both support it.

Two entry points, mirroring diagnosis_engine.py's shape:

  build_intervention_context(field)
      Read-only. Assembles crop/stage/risk/diagnosis/affected-area/
      history context for the "Recommended Intervention" panel.

  recommend_interventions(field)
      Runs the decision cascade below and returns the pathway + any
      matching approved-use records + reasons/warnings.

  simulate_intervention(pesticide_use, affected_area_ha)
      Transparent planning-calculator math (dosage-range x area), never
      a biological outcome simulation.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from backend.models import PesticideUse
from backend.services.pesticide_data_service import search_pesticides_any_pest

# Damage patterns (see diagnosis_engine.DAMAGE_PATTERN_HINTS) that are
# plausibly insect-pest activity vs. patterns that point somewhere the
# approved-use INSECTICIDE dataset cannot help.
PEST_PLAUSIBLE_PATTERNS = {"chewing", "sucking", "curling"}
DISEASE_PATTERN = "leaf_spot"          # fungal/bacterial -- outside this (insecticide-only) dataset
WATER_STRESS_PATTERN = "wilting"       # irrigation issue, not a pesticide target
NUTRIENT_PATTERN = "discoloration"     # nutrient/environmental, not a pesticide target

# damage pattern -> plausible common pest-name keywords to search the
# approved-use dataset with (backend/services/pesticide_data_service.py
# does the actual crop+pest matching against real rows -- this is only
# the candidate list of names to try).
DAMAGE_PATTERN_PEST_CANDIDATES = {
    "chewing": ["bollworm", "pod borer", "fruit borer", "stem borer", "leaf folder",
                "caterpillar", "borer", "hispa", "weevil"],
    "sucking": ["aphid", "thrips", "jassid", "whitefly", "mite", "hopper"],
    "curling": ["aphid", "thrips", "whitefly", "mite"],
}

STRESSED_FRACTION_FIELD_WIDE_THRESHOLD = 0.60
HIGH_RISK_LEVELS = {"high", "critical"}


def _num(value, default=None):
    return value if isinstance(value, (int, float)) else default

def build_intervention_context(field) -> Dict:
    """Read-only assembly of intervention context."""

    analysis = field.latest_analysis()
    risk = field.latest_risk_assessment()
    diagnosis = field.latest_diagnosis()

    analysis_dict = analysis.to_dict(include_zones=False) if analysis else None
    risk_dict = risk.to_dict() if risk else None
    diagnosis_dict = diagnosis.to_dict(include_evidence=False) if diagnosis else None

    zone_stats = (analysis_dict or {}).get("zone_stats") or {}
    stressed = zone_stats.get("stressed") or 0
    total = zone_stats.get("total") or 0

    affected_area_ha = None
    affected_area_basis = None

    if analysis and total:
        affected_area_ha = (
            round((field.area_ha or 0) * (stressed / total), 3)
            if field.area_ha else None
        )
        affected_area_basis = (
            "field area x stressed-zone fraction from the latest analysis"
        )

    history_note = None

    if diagnosis and diagnosis.possible_cause:
        prior = [
            d for d in field.diagnoses.limit(10).all()
            if d.id != diagnosis.id and d.possible_cause
        ]

        keyword = _leading_cause_keyword(diagnosis.possible_cause)

        if keyword and any(
            keyword in (d.possible_cause or "").lower()
            for d in prior
        ):
            history_note = (
                f"Similar stress ('{keyword}') was previously recorded in this "
                f"field's diagnosis history. This is supporting context only -- "
                f"it does not confirm the same pest is present again."
            )

    return {
        "field": {
            "id": field.id,
            "name": field.name,
            "crop_type": field.crop_type,
            "crop_stage": field.crop_stage,
            "area_ha": field.area_ha,
        },
        "risk": risk_dict,
        "diagnosis": diagnosis_dict,
        "affected_zones": {
            "stressed": stressed,
            "total": total
        },
        "affected_area_ha": affected_area_ha,
        "affected_area_basis": affected_area_basis,
        "history_note": history_note,
        "intervention_readiness": (
            "ready"
            if diagnosis and diagnosis.status == "diagnosed"
            else "needs_diagnosis"
        ),
    }

def _leading_cause_keyword(possible_cause: str) -> Optional[str]:
    text = (possible_cause or "").lower()

    for kw in (
        "sucking-pest",
        "chewing-pest",
        "water stress",
        "fungal",
        "bacterial",
        "nutrient deficiency",
        "viral",
    ):
        if kw in text:
            return kw

    return None


def _damage_patterns_for(diagnosis) -> set:
    return {
        item.damage_pattern
        for item in (diagnosis.evidence_items or [])
        if item.damage_pattern
    }

def recommend_interventions(field) -> Dict:
    """The core decision cascade. Returns:
      {
        recommended_pathway: 'monitor' | 'verify' | 'targeted',
        intervention_appropriate: bool,
        urgency: 'none'|'low'|'medium'|'high',
        affected_area_ha, affected_area_basis,
        matches: [PesticideUse.to_dict(), ...],
        reasons: [str, ...],
        warnings: [str, ...],
        confidence_note: str,
      }
    """

    context = build_intervention_context(field)
    diagnosis = field.latest_diagnosis()
    reasons: List[str] = []
    warnings: List[str] = []

    if not diagnosis or diagnosis.status != "diagnosed":
        return {
            "recommended_pathway": "monitor",
            "intervention_appropriate": False,
            "urgency": "none",
            "affected_area_ha": context["affected_area_ha"],
            "affected_area_basis": context["affected_area_basis"],
            "matches": [],
            "reasons": [
                "No completed diagnosis exists for this field yet. Satellite stress alone "
                "is not sufficient evidence for a pesticide recommendation.",
            ],
            "warnings": [],
            "confidence_note": "Run 'Diagnose This Field' first to enable intervention guidance.",
            "history_note": context["history_note"],
        }

    risk_level = (
        (diagnosis.context_snapshot or {}).get("risk") or {}
    ).get("risk_level", "low")

    confidence_level = (
        diagnosis.confidence_level or "low"
    ).lower()

    damage_patterns = _damage_patterns_for(diagnosis)
    crop = field.crop_type

    has_visual_pest_detection = any(
        d.get("detected")
        for d in (diagnosis.pest_detection or [])
    )

    has_pest_pattern = bool(
        damage_patterns & PEST_PLAUSIBLE_PATTERNS
    )

    intervention_relevant = (
        has_pest_pattern or
        has_visual_pest_detection
    )

    has_visual_disease_detection = any(
        d.get("detected")
        for d in (diagnosis.disease_detection or [])
    )

    has_disease_pattern = (
        DISEASE_PATTERN in damage_patterns
        or has_visual_disease_detection
    )

    has_water_pattern = WATER_STRESS_PATTERN in damage_patterns
    has_nutrient_pattern = NUTRIENT_PATTERN in damage_patterns

    # ------------------------------------------------------------
    # 2. Non-pest hypotheses
    # ------------------------------------------------------------

    if not intervention_relevant:
        return {
            "recommended_pathway": "monitor",
            "intervention_appropriate": False,
            "urgency": "low",
            "affected_area_ha": context["affected_area_ha"],
            "affected_area_basis": context["affected_area_basis"],
            "matches": [],
            "reasons": [
                "The current diagnosis does not provide sufficient evidence that "
                "a pesticide intervention is relevant."
            ],
            "warnings": [],
            "confidence_note": (
                "Monitor the affected zones and verify the suspected cause before treatment."
            ),
            "history_note": context["history_note"],
        }

    if has_water_pattern and not has_pest_pattern:
        return {
            "recommended_pathway": "verify",
            "intervention_appropriate": False,
            "urgency": "low",
            "affected_area_ha": context["affected_area_ha"],
            "affected_area_basis": context["affected_area_basis"],
            "matches": [],
            "reasons": [
                "The diagnosis points to possible water stress, not a pest. "
                "No pesticide is relevant here.",
            ],
            "warnings": [],
            "confidence_note": (
                "Verify soil moisture / irrigation before considering any treatment."
            ),
            "history_note": context["history_note"],
        }

    if has_disease_pattern and not has_visual_pest_detection:
        return {
            "recommended_pathway": "verify",
            "intervention_appropriate": False,
            "urgency": "medium" if risk_level in HIGH_RISK_LEVELS else "low",
            "affected_area_ha": context["affected_area_ha"],
            "affected_area_basis": context["affected_area_basis"],
            "matches": [],
            "reasons": [
                "The diagnosis points to a possible fungal/bacterial leaf disease. "
                "The current approved-use dataset is insecticide-focused -- it does "
                "not cover fungicide or bactericide recommendations.",
            ],
            "warnings": [
                "Additional disease-control (fungicide/bactericide) reference data "
                "would be needed before this system can suggest a treatment for a disease.",
            ],
            "confidence_note": (
                "Confirm the disease with a local agronomist or extension service."
            ),
            "history_note": context["history_note"],
        }

    if has_nutrient_pattern and not has_pest_pattern:
        return {
            "recommended_pathway": "verify",
            "intervention_appropriate": False,
            "urgency": "low",
            "affected_area_ha": context["affected_area_ha"],
            "affected_area_basis": context["affected_area_basis"],
            "matches": [],
            "reasons": [
                "The diagnosis points to a possible nutrient deficiency or "
                "environmental stress, not a pest. No pesticide is relevant here.",
            ],
            "warnings": [],
            "confidence_note": (
                "Consider a soil/leaf nutrient test before any treatment decision."
            ),
            "history_note": context["history_note"],
        }

    # ------------------------------------------------------------
    # 3. Insufficient evidence
    # ------------------------------------------------------------

    if not has_pest_pattern and confidence_level == "low":
        return {
            "recommended_pathway": "monitor",
            "intervention_appropriate": False,
            "urgency": "low",
            "affected_area_ha": context["affected_area_ha"],
            "affected_area_basis": context["affected_area_basis"],
            "matches": [],
            "reasons": [
                "Cause is not sufficiently specific for a pesticide recommendation.",
            ],
            "warnings": [],
            "confidence_note": (
                "Inspect the affected zones, collect further evidence, and monitor."
            ),
            "history_note": context["history_note"],
        }

    # ------------------------------------------------------------
    # 4. Pest-plausible: search approved-use dataset
    # ------------------------------------------------------------

    candidate_pests = _extract_pest_candidates(
        diagnosis,
        damage_patterns
    )

    for detection in (diagnosis.pest_detection or []):
        pest_name = detection.get("name")
        if pest_name:
            candidate_pests.insert(0, pest_name)

    if not candidate_pests:
        return {
            "recommended_pathway": "verify",
            "intervention_appropriate": False,
            "urgency": "medium" if risk_level in HIGH_RISK_LEVELS else "low",
            "affected_area_ha": context["affected_area_ha"],
            "affected_area_basis": context["affected_area_basis"],
            "matches": [],
            "reasons": [
                "The diagnosis suggests possible pest activity, but the suspected "
                "pest category is not specific enough to safely query the approved-use dataset."
            ],
            "warnings": [
                "Confirm the pest on the ground before treatment."
            ],
            "confidence_note": (
                "Collect additional field evidence before selecting an intervention."
            ),
            "history_note": context["history_note"],
        }

    matches = search_pesticides_any_pest(
        crop,
        candidate_pests,
        limit=25
    )

    if not matches:
        return {
            "recommended_pathway": "verify",
            "intervention_appropriate": False,
            "urgency": "medium" if risk_level in HIGH_RISK_LEVELS else "low",
            "affected_area_ha": context["affected_area_ha"],
            "affected_area_basis": context["affected_area_basis"],
            "matches": [],
            "reasons": [
                f"Diagnosis suggests possible pest activity, but no approved-use "
                f"record was found in the dataset for {crop} matching the suspected "
                f"pest category.",
            ],
            "warnings": [
                "This does not mean no treatment exists -- only that this dataset "
                "has no matching entry. Consult local agricultural extension guidance.",
            ],
            "confidence_note": (
                "Confirm the specific pest on the ground before any treatment."
            ),
            "history_note": context["history_note"],
        }

    if confidence_level == "low":
        return {
            "recommended_pathway": "verify",
            "intervention_appropriate": False,
            "urgency": "low",
            "affected_area_ha": context["affected_area_ha"],
            "affected_area_basis": context["affected_area_basis"],
            "matches": [m.to_dict() for m in matches],
            "reasons": [
                "Diagnosis confidence is low. The records below are approved-use "
                "matches for the suspected pest category, shown for reference -- "
                "confirm the pest before treating.",
            ],
            "warnings": [],
            "confidence_note": (
                "Confirm pest/disease presence before selecting a treatment."
            ),
            "history_note": context["history_note"],
        }

    # ------------------------------------------------------------
    # 5. Medium/high confidence + pest match
    #
    # IMPORTANT:
    # recommended_pathway is ONLY AgroSentinel's recommendation.
    # It does NOT determine what the farmer is allowed to select.
    #
    # Targeted treatment remains the recommended pathway.
    # Field-wide treatment is only an available alternative.
    # ------------------------------------------------------------

    stressed = context["affected_zones"]["stressed"]
    total = context["affected_zones"]["total"]

    stressed_fraction = (
        stressed / total
        if total
        else None
    )

    field_wide_available = bool(
        stressed_fraction is not None
        and stressed_fraction >= STRESSED_FRACTION_FIELD_WIDE_THRESHOLD
        and risk_level in HIGH_RISK_LEVELS
    )

    # Recommendation ≠ farmer choice.
    recommended_pathway = "targeted"

    urgency = (
        "high"
        if risk_level in HIGH_RISK_LEVELS
        else "medium"
    )

    reasons.append(
        f"Diagnosis indicates possible pest activity "
        f"({', '.join(sorted(damage_patterns)) or 'pest-consistent pattern'}) "
        f"with {confidence_level} confidence, and {len(matches)} "
        f"approved-use record(s) match {crop} and the suspected pest category."
    )

    if field_wide_available:
        reasons.append(
            f"{stressed}/{total} zones are stressed and risk is '{risk_level}', "
            f"so field-wide treatment can also be considered. Targeted treatment "
            f"of affected zones remains the recommended approach."
        )

    warnings.extend([
        "These are approved-use reference records matching the selected crop "
        "and suspected pest category.",
        "Confirm the pest on the ground before treatment.",
        "Follow the applicable product label and local agricultural guidance.",
        "Target affected zones first. Do not treat the entire field unless "
        "field-wide treatment is justified."
    ])

    return {
        "recommended_pathway": recommended_pathway,
        "intervention_appropriate": True,
        "urgency": urgency,
        "affected_area_ha": context["affected_area_ha"],
        "affected_area_basis": context["affected_area_basis"],
        "matches": [m.to_dict() for m in matches],
        "reasons": reasons,
        "warnings": warnings,
        "confidence_note": f"Diagnosis confidence: {confidence_level}.",
        "history_note": context["history_note"],

        # This is availability, NOT farmer selection.
        "field_wide_available": field_wide_available,

        "targeting": {
            "default": "affected_zones",
            "affected_zones": context["affected_zones"],
            "affected_area_ha": context["affected_area_ha"],
            "field_wide_available": field_wide_available,
            "field_wide_reason": (
                "Available because the stressed-zone fraction and risk level "
                "support considering broader intervention."
                if field_wide_available
                else "Not justified by the current stressed-zone extent/risk."
            ),
        },
    }

def _extract_pest_candidates(diagnosis, damage_patterns):
    candidates = []

    for detection in (diagnosis.pest_detection or []):
        pest_name = detection.get("name")
        if pest_name:
            candidates.append(pest_name)

    possible_cause = (diagnosis.possible_cause or "").strip()

    if possible_cause:
        cleaned = re.sub(r"\([^)]*\)", "", possible_cause)
        cleaned = re.sub(r"[^a-zA-Z0-9\s-]", " ", cleaned)

        phrases = [
            phrase.strip()
            for phrase in re.split(
                r",|;|\band\b|\bor\b",
                cleaned,
                flags=re.I
            )
            if phrase.strip()
        ]

        for phrase in phrases:
            if len(phrase.split()) <= 8:
                candidates.append(phrase)

    for pattern in damage_patterns:
        candidates.extend(
            DAMAGE_PATTERN_PEST_CANDIDATES.get(pattern, [])
        )

    seen = set()
    result = []

    for item in candidates:
        key = item.lower().strip()

        if key and key not in seen:
            seen.add(key)
            result.append(item)

    return result


# ---------------------------------------------------------- simulator -----
_PER_UNIT_NOT_AREA_RE = re.compile(r"/\s*(tree|plant|suckers?)", re.I)
_NUMBER_RE = re.compile(r"\d+\.?\d*")


def _parse_reference_range(text: Optional[str]):
    """Extracts a (min, max) numeric reference range from a free-text
    dataset field like '500-750', '10 to 20', '0.0005', or '_' (empty).
    Never invents a number -- returns None when nothing usable is
    present, and flags per-tree/per-plant units as non-area-scalable
    rather than silently misapplying them."""

    if not text:
        return None, "Not specified in the approved-use record."

    if _PER_UNIT_NOT_AREA_RE.search(text):
        return None, f"Reference value ('{text}') is per plant/tree, not per hectare -- cannot be scaled by selected area."

    numbers = [float(n) for n in _NUMBER_RE.findall(text)]
    if not numbers:
        return None, f"Could not parse a numeric reference from '{text}'."

    return (min(numbers), max(numbers)), None


def simulate_intervention(pesticide_use: PesticideUse, affected_area_ha: float) -> Dict:
    """Transparent planning calculator: reference-range x selected area.
    NEVER a biological-outcome simulation (no mortality %, no efficacy
    estimate) -- see module docstring / project brief section 11."""

    result = {
    "pesticide_use": pesticide_use.to_dict(),
    "area_ha": affected_area_ha,
    "planning": {
        "affected_area_ha": affected_area_ha,
        "formulation_min": None,
        "formulation_max": None,
        "spray_fluid_min": None,
        "spray_fluid_max": None,
    },
    "formulation_note": None,
    "spray_fluid_note": None,
    "disclaimer": (
        "Planning/reference information only. It is calculated from "
        "the approved-use dataset reference range multiplied by the "
        "selected area. It is not an automatic prescription or "
        "guaranteed treatment dose. Always follow the applicable "
        "product label and local agricultural guidance."
    ),
    }

    formulation_range, formulation_note = _parse_reference_range(pesticide_use.formulation_dosage)
    if formulation_range:
        lo, hi = formulation_range
        result["planning"]["formulation_min"] = round(
            lo * affected_area_ha, 3
        )
        result["planning"]["formulation_max"] = round(
            hi * affected_area_ha, 3
        )
        result["formulation_reference_per_ha"] = pesticide_use.formulation_dosage
    result["formulation_note"] = formulation_note

    spray_range, spray_note = _parse_reference_range(pesticide_use.spray_fluid)
    if spray_range:
        lo, hi = spray_range
        result["planning"]["spray_fluid_min"] = round(
            lo * affected_area_ha, 3
        )
        result["planning"]["spray_fluid_max"] = round(
            hi * affected_area_ha, 3
        )
        result["spray_fluid_reference_per_ha"] = pesticide_use.spray_fluid
    result["spray_fluid_note"] = spray_note

    return result


# ------------------------------------------------- record / follow-up -----
# Everything below supports Stage 4 (farmer chooses -> record -> follow-up
# -> before/after -> outcome). None of it re-decides MONITOR/VERIFY/
# TARGETED/FIELD_WIDE -- that decision is made exactly once by
# recommend_interventions() above and is only ever *frozen* here
# (backend/models.py::Intervention.pathway_at_decision), never re-derived.

# Suggested reassessment windows, in days. These are NOT biological
# response-time claims ("the pesticide will work in X days") -- they are
# how soon it is useful to re-run the existing satellite/weather/IoT
# analysis pipeline to see whether field indicators have moved at all.
# Kept short and generic on purpose since this project has no per-product
# label/efficacy dataset to draw a real interval from (see module
# docstring, "never invents ... treatment efficacy").
FOLLOWUP_WINDOW_DAYS = {
    "targeted": (5, 7),
    "field_wide": (5, 7),
    "monitor": (7, 10),
}


def compute_followup_window(selected_option: str) -> Dict:
    """Returns {"min_days", "max_days", "note"} for the chosen option.
    'verify' is not a selectable option (see routes/intervention.py) so it
    is not in FOLLOWUP_WINDOW_DAYS; callers should not reach here for it.
    """
    min_days, max_days = FOLLOWUP_WINDOW_DAYS.get(selected_option, (7, 10))
    return {
        "min_days": min_days,
        "max_days": max_days,
        "note": (
            f"Suggested follow-up/reassessment window: approximately {min_days}-{max_days} days. "
            "This is when re-running field analysis is likely to show a meaningful change -- "
            "not a claim about how quickly any treatment takes effect."
        ),
    }


def estimate_cost(simulation: Dict, price_per_unit_inr: Optional[float] = None) -> Dict:
    """Transparent cost planning layer on top of simulate_intervention()'s
    quantity output. The approved-use dataset (PesticideUse) has NO price
    column -- inventing a market price per product would be exactly the
    kind of fabricated precision the project brief forbids. Instead this
    multiplies the already-computed formulation-quantity range by a price
    the FARMER supplies (their own local market rate), which keeps every
    number in the result traceable to either the dataset or the farmer's
    own input -- never a guessed figure.

    Returns cost_min/cost_max = None (with an explanatory note) when no
    price was supplied, rather than silently defaulting to some invented
    average price.
    """
    planning = (simulation or {}).get("planning") or {}
    qty_min = planning.get("formulation_min")
    qty_max = planning.get("formulation_max")

    if price_per_unit_inr is None or qty_min is None or qty_max is None:
        return {
            "cost_min_inr": None,
            "cost_max_inr": None,
            "price_per_unit_inr": price_per_unit_inr,
            "note": (
                "Enter a local price per unit of formulation product to see an approximate "
                "planning cost. This dataset does not include pesticide pricing, so no cost "
                "figure is shown without a price you supply."
                if price_per_unit_inr is None
                else "Formulation quantity could not be determined from the approved-use record, "
                     "so a cost estimate is not available."
            ),
        }

    price = max(0.0, float(price_per_unit_inr))
    return {
        "cost_min_inr": round(qty_min * price, 2),
        "cost_max_inr": round(qty_max * price, 2),
        "price_per_unit_inr": price,
        "note": (
            "Approximate planning cost = approved-use formulation-quantity range x your "
            "entered local price. Not a guaranteed or prescribed cost."
        ),
    }


def _snapshot_from(analysis, risk) -> Dict:
    """Flat, comparison-friendly snapshot of one Analysis + RiskAssessment
    pair. Used identically for BEFORE (at record time) and AFTER (at
    follow-up time) so the two are always directly comparable field-by-field.
    """
    if analysis is None:
        return {
            "available": False,
            "analysis_id": None,
            "risk_assessment_id": None,
            "risk_level": None,
            "stressed_zones": None,
            "total_zones": None,
            "mean_ndvi": None,
            "affected_area_ha": None,
            "observed_at": None,
        }
    a = analysis.to_dict(include_zones=False)
    zone_stats = a.get("zone_stats") or {}
    return {
        "available": True,
        "analysis_id": analysis.id,
        "risk_assessment_id": risk.id if risk else None,
        "risk_level": risk.risk_level if risk else None,
        "stressed_zones": zone_stats.get("stressed"),
        "total_zones": zone_stats.get("total"),
        "mean_ndvi": a.get("mean_ndvi"),
        "affected_area_ha": a.get("analyzed_area_ha"),
        "observed_at": a.get("created_at"),
    }


def build_before_snapshot(field) -> Dict:
    """Freezes the field's CURRENT analysis + risk assessment as the BEFORE
    state, at the moment the farmer records an intervention. Called once,
    from the /intervention/record route -- the resulting dict is stored on
    Intervention.before_snapshot and must never be recomputed/overwritten
    later, even if the field is re-analyzed many times afterward."""
    return _snapshot_from(field.latest_analysis(), field.latest_risk_assessment())


def build_after_snapshot(analysis, risk) -> Dict:
    """Same shape as build_before_snapshot, built from the specific
    Analysis/RiskAssessment produced by the follow-up re-analysis run
    (see routes/intervention.py::api_run_followup), not just 'whatever is
    latest' -- so it stays correct even if the farmer analyzes the field
    again afterward for unrelated reasons."""
    return _snapshot_from(analysis, risk)


_RISK_ORDER = {"low": 0, "moderate": 1, "high": 2, "critical": 3}


def evaluate_outcome(before_snapshot: Dict, after_snapshot: Dict) -> Dict:
    """Explainable BEFORE vs AFTER comparison. Never claims causality
    ("the pesticide cured the field") -- only describes whether field
    indicators moved, in which direction, following the recorded
    intervention. See project brief 'OUTCOME LANGUAGE'.
    """
    before = before_snapshot or {}
    after = after_snapshot or {}

    if not before.get("available") or not after.get("available"):
        return {
            "outcome": "insufficient_data",
            "explanation": (
                "Insufficient data to compare before and after this intervention. "
                "Continue monitoring and run another analysis when possible."
            ),
        }

    b_stressed, a_stressed = before.get("stressed_zones"), after.get("stressed_zones")
    b_ndvi, a_ndvi = before.get("mean_ndvi"), after.get("mean_ndvi")
    b_risk, a_risk = before.get("risk_level"), after.get("risk_level")

    stressed_delta = (
        (a_stressed - b_stressed)
        if isinstance(a_stressed, (int, float)) and isinstance(b_stressed, (int, float))
        else None
    )
    ndvi_delta = (
        (a_ndvi - b_ndvi)
        if isinstance(a_ndvi, (int, float)) and isinstance(b_ndvi, (int, float))
        else None
    )
    b_rank = _RISK_ORDER.get((b_risk or "").lower())
    a_rank = _RISK_ORDER.get((a_risk or "").lower())
    risk_delta = (a_rank - b_rank) if b_rank is not None and a_rank is not None else None

    if stressed_delta is None and ndvi_delta is None and risk_delta is None:
        return {
            "outcome": "insufficient_data",
            "explanation": (
                "Insufficient comparable data between the before and after analyses. "
                "Continue verification and monitoring."
            ),
        }

    improved = (
        (stressed_delta is not None and stressed_delta < 0)
        or (ndvi_delta is not None and ndvi_delta > 0.01)
        or (risk_delta is not None and risk_delta < 0)
    )
    worsened = (
        (stressed_delta is not None and stressed_delta > 0)
        or (ndvi_delta is not None and ndvi_delta < -0.01)
        or (risk_delta is not None and risk_delta > 0)
    )

    if improved and not worsened:
        outcome = "positive"
        explanation = (
            "Field indicators improved following the recorded intervention "
            f"(stressed zones {b_stressed} -> {a_stressed}, mean NDVI "
            f"{b_ndvi} -> {a_ndvi}, risk {b_risk} -> {a_risk}). "
            "Continued monitoring is still recommended."
        )
    elif worsened and not improved:
        outcome = "worsened"
        explanation = (
            "Field stress increased during the follow-up analysis "
            f"(stressed zones {b_stressed} -> {a_stressed}, mean NDVI "
            f"{b_ndvi} -> {a_ndvi}, risk {b_risk} -> {a_risk}). "
            "Further investigation is recommended."
        )
    elif improved and worsened:
        outcome = "limited"
        explanation = (
            "Indicators moved in mixed directions following the recorded intervention "
            f"(stressed zones {b_stressed} -> {a_stressed}, mean NDVI {b_ndvi} -> {a_ndvi}, "
            f"risk {b_risk} -> {a_risk}). The response is unclear -- continue verification "
            "and monitoring."
        )
    else:
        outcome = "no_improvement"
        explanation = (
            "No clear improvement was detected during the follow-up analysis "
            f"(stressed zones {b_stressed} -> {a_stressed}, mean NDVI {b_ndvi} -> {a_ndvi}, "
            f"risk {b_risk} -> {a_risk}). Continue verification and monitoring."
        )

    return {"outcome": outcome, "explanation": explanation}