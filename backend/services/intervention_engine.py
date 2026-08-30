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
    """Read-only assembly of the 'Recommended Intervention' panel's
    input context. Pulls only from data the existing pipeline already
    produced (Analysis, RiskAssessment, latest FieldDiagnosis) -- no new
    analysis or diagnosis is triggered here."""

    analysis = field.latest_analysis()
    risk = field.latest_risk_assessment()
    diagnosis = field.latest_diagnosis()

    analysis_dict = analysis.to_dict(include_zones=False) if analysis else None
    risk_dict = risk.to_dict() if risk else None
    diagnosis_dict = diagnosis.to_dict(include_evidence=False) if diagnosis else None

    zone_stats = (analysis_dict or {}).get("zone_stats") or {}
    stressed = zone_stats.get("stressed") or 0
    total = zone_stats.get("total") or 0
    stressed_fraction = (stressed / total) if total else None

    # Affected area: prefer the analyzed stressed-zone area if available,
    # else fall back to a fraction-of-field-area estimate. Both are
    # clearly labeled so the simulator never implies false precision.
    affected_area_ha = None
    affected_area_basis = None
    if analysis and total:
        affected_area_ha = round((field.area_ha or 0) * (stressed / total), 3) if field.area_ha else None
        affected_area_basis = "field area x stressed-zone fraction from the latest analysis"

    # History: has a similar cause/pattern been seen before on this field?
    # Informational only -- never treated as proof the same pest recurred.
    history_note = None
    if diagnosis and diagnosis.possible_cause:
        # Simple containment check against prior diagnoses' possible_cause,
        # avoiding extra query complexity for this prototype.
        prior = [
            d for d in field.diagnoses.limit(10).all()
            if d.id != diagnosis.id and d.possible_cause
        ]
        keyword = _leading_cause_keyword(diagnosis.possible_cause)
        if keyword and any(keyword in (d.possible_cause or "").lower() for d in prior):
            history_note = (
                f"Similar stress ('{keyword}') was previously recorded in this field's "
                f"diagnosis history. This is supporting context only -- it does not confirm "
                f"the same pest is present again."
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
        "affected_zones": {"stressed": stressed, "total": total},
        "affected_area_ha": affected_area_ha,
        "affected_area_basis": affected_area_basis,
        "history_note": history_note,
        "intervention_readiness": (
            "ready" if diagnosis and diagnosis.status == "diagnosed" else "needs_diagnosis"
        ),
    }


def _leading_cause_keyword(possible_cause: str) -> Optional[str]:
    text = (possible_cause or "").lower()
    for kw in ("sucking-pest", "chewing-pest", "water stress", "fungal", "bacterial",
               "nutrient deficiency", "viral"):
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
        pathway: 'monitor' | 'verify' | 'targeted' | 'field_wide_available',
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

    # ---- 1. No diagnosis yet -> can't responsibly say anything more than "monitor/diagnose" ----
    if not diagnosis or diagnosis.status != "diagnosed":
        return {
            "pathway": "monitor",
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

    risk_level = ((diagnosis.context_snapshot or {}).get("risk") or {}).get("risk_level", "low")
    confidence_level = (diagnosis.confidence_level or "low").lower()
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

    # ---- 2. Non-pest hypotheses: never search the (insecticide-only) dataset ----
    if not intervention_relevant:
        return {
        "pathway": "monitor",
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
            "pathway": "verify",
            "intervention_appropriate": False,
            "urgency": "low",
            "affected_area_ha": context["affected_area_ha"],
            "affected_area_basis": context["affected_area_basis"],
            "matches": [],
            "reasons": [
                "The diagnosis points to possible water stress, not a pest. No pesticide is "
                "relevant here.",
            ],
            "warnings": [],
            "confidence_note": "Verify soil moisture / irrigation before considering any treatment.",
            "history_note": context["history_note"],
        }

    if has_disease_pattern and not has_visual_pest_detection:
        return {
            "pathway": "verify",
            "intervention_appropriate": False,
            "urgency": "medium" if risk_level in HIGH_RISK_LEVELS else "low",
            "affected_area_ha": context["affected_area_ha"],
            "affected_area_basis": context["affected_area_basis"],
            "matches": [],
            "reasons": [
                "The diagnosis points to a possible fungal/bacterial leaf disease. The current "
                "approved-use dataset is insecticide-focused -- it does not cover fungicide or "
                "bactericide recommendations.",
            ],
            "warnings": [
                "Additional disease-control (fungicide/bactericide) reference data would be "
                "needed before this system can suggest a treatment for a disease.",
            ],
            "confidence_note": "Confirm the disease with a local agronomist or extension service.",
            "history_note": context["history_note"],
        }

    if has_nutrient_pattern and not has_pest_pattern:
        return {
            "pathway": "verify",
            "intervention_appropriate": False,
            "urgency": "low",
            "affected_area_ha": context["affected_area_ha"],
            "affected_area_basis": context["affected_area_basis"],
            "matches": [],
            "reasons": [
                "The diagnosis points to a possible nutrient deficiency or environmental "
                "stress, not a pest. No pesticide is relevant here.",
            ],
            "warnings": [],
            "confidence_note": "Consider a soil/leaf nutrient test before any treatment decision.",
            "history_note": context["history_note"],
        }

    # ---- 3. Not enough evidence at all (low confidence, no named pattern) ----
    if not has_pest_pattern and confidence_level == "low":
        return {
            "pathway": "monitor",
            "intervention_appropriate": False,
            "urgency": "low",
            "affected_area_ha": context["affected_area_ha"],
            "affected_area_basis": context["affected_area_basis"],
            "matches": [],
            "reasons": [
                "Cause is not sufficiently specific for a pesticide recommendation.",
            ],
            "warnings": [],
            "confidence_note": "Inspect the affected zones, collect further evidence, and monitor.",
            "history_note": context["history_note"],
        }

    # ---- 4. Pest-plausible: search the approved-use dataset ----
    candidate_pests: List[str] = []
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
        "pathway": "verify",
        "intervention_appropriate": False,
        "urgency": "medium" if risk_level in HIGH_RISK_LEVELS else "low",
        "affected_area_ha": context["affected_area_ha"],
        "affected_area_basis": context["affected_area_basis"],
        "matches": [],
        "reasons": [
            "The diagnosis suggests possible pest activity, but the suspected pest "
            "category is not specific enough to safely query the approved-use dataset."
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
            "pathway": "verify",
            "intervention_appropriate": False,
            "urgency": "medium" if risk_level in HIGH_RISK_LEVELS else "low",
            "affected_area_ha": context["affected_area_ha"],
            "affected_area_basis": context["affected_area_basis"],
            "matches": [],
            "reasons": [
                f"Diagnosis suggests possible pest activity, but no approved-use record was "
                f"found in the dataset for {crop} matching the suspected pest category.",
            ],
            "warnings": [
                "This does not mean no treatment exists -- only that this dataset has no "
                "matching entry. Consult local agricultural extension guidance.",
            ],
            "confidence_note": "Confirm the specific pest on the ground before any treatment.",
            "history_note": context["history_note"],
        }

    if confidence_level == "low":
        # Evidence points toward a pest but the diagnosis engine itself
        # is still low-confidence -- show matches for information, but
        # do NOT call it a targeted intervention yet.
        return {
            "pathway": "verify",
            "intervention_appropriate": False,
            "urgency": "low",
            "affected_area_ha": context["affected_area_ha"],
            "affected_area_basis": context["affected_area_basis"],
            "matches": [m.to_dict() for m in matches],
            "reasons": [
                "Diagnosis confidence is low. The records below are approved-use matches for "
                "the suspected pest category, shown for reference -- confirm the pest before "
                "treating.",
            ],
            "warnings": [],
            "confidence_note": "Confirm pest/disease presence before selecting a treatment.",
            "history_note": context["history_note"],
        }

    # ---- 5. Medium/high confidence + pest match found: targeted, with field-wide as an option ----
    stressed = context["affected_zones"]["stressed"]
    total = context["affected_zones"]["total"]
    stressed_fraction = (stressed / total) if total else None

    field_wide_available = bool(
        stressed_fraction is not None
        and stressed_fraction >= STRESSED_FRACTION_FIELD_WIDE_THRESHOLD
        and risk_level in HIGH_RISK_LEVELS
    )

    pathway = "field_wide_available" if field_wide_available else "targeted"
    urgency = "high" if risk_level in HIGH_RISK_LEVELS else "medium"

    reasons.append(
        f"Diagnosis indicates possible pest activity ({', '.join(sorted(damage_patterns)) or 'pest-consistent pattern'}) "
        f"with {confidence_level} confidence, and {len(matches)} approved-use record(s) match "
        f"{crop} and the suspected pest category."
    )
    if field_wide_available:
        reasons.append(
            f"{stressed}/{total} zones are stressed and risk is '{risk_level}', which supports "
            f"considering field-wide treatment as an option -- targeted treatment of the "
            f"affected zones is still the preferred default."
        )

    warnings.extend([
        "These are approved-use reference records matching the selected crop and suspected pest category.",
        "Confirm the pest on the ground before treatment.",
        "Follow the applicable product label and local agricultural guidance.",
        "Target affected zones first. Do not treat the entire field unless field-wide treatment is justified."
    ])

    return {
        "pathway": pathway,
        "intervention_appropriate": True,
        "urgency": urgency,
        "affected_area_ha": context["affected_area_ha"],
        "affected_area_basis": context["affected_area_basis"],
        "matches": [m.to_dict() for m in matches],
        "reasons": reasons,
        "warnings": warnings,
        "confidence_note": f"Diagnosis confidence: {confidence_level}.",
        "history_note": context["history_note"],
        "field_wide_available": field_wide_available,
        "targeting": {
            "default": "affected_zones",
            "affected_zones": context["affected_zones"],
            "affected_area_ha": context["affected_area_ha"],
            "field_wide_available": field_wide_available,
            "field_wide_reason": (
                "Available only because the stressed-zone fraction and "
                "risk level support considering broader intervention."
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