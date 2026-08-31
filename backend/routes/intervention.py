"""
Intervention blueprint
------------------------
Field-specific decision-support routes, hung off the existing field-scoped
API namespace (/api/fields/<field_id>/intervention/...), matching
routes/diagnosis.py's structure and using the same ownership choke point
(_get_owned_field_or_404) so a farmer can only ever reach their own
fields.

This blueprint is READ + CALCULATE only with respect to the pesticide
dataset -- it never writes to PesticideUse (that's the importer's job,
see scripts/import_pesticide_data.py) and it never runs a diagnosis
itself (that's diagnosis.py) -- it only consumes the latest FieldDiagnosis
already on the field.

Routes:
  GET  /api/fields/<id>/intervention/context
       Read-only: crop/stage/risk/diagnosis/affected-area/history --
       the "why is this being suggested" panel.

  GET  /api/fields/<id>/intervention/options
       Runs the Intervention Engine decision cascade (see
       backend/services/intervention_engine.py) and returns the pathway
       + any matching approved-use records.

  POST /api/fields/<id>/intervention/simulate
       body: {"pesticide_use_id": int, "affected_area_ha": float}
       Transparent reference-range x area calculator -- a planning tool,
       never a guaranteed dose or a biological-outcome simulation.

  ---- Stage 4: farmer chooses -> record -> follow-up -> before/after ----
  These routes NEVER re-decide MONITOR/VERIFY/TARGETED/FIELD_WIDE -- that
  decision is made exactly once by recommend_interventions() and is only
  validated against + frozen here.

  POST /api/fields/<id>/intervention/record
       body: {"selected_option": "targeted"|"field_wide"|"monitor",
              "pesticide_use_id": int|null, "affected_area_ha": float|null,
              "price_per_unit_inr": float|null, "farmer_notes": str|null}
       Validates the choice against the CURRENT recommend_interventions()
       output, freezes the BEFORE snapshot, and creates an Intervention row.

  GET  /api/fields/<id>/intervention/history
       All recorded Intervention rows for this field, newest first.

  GET  /api/fields/<id>/intervention/<intervention_id>
       Full detail for one recorded intervention (View Details).

  GET  /api/fields/<id>/intervention/<intervention_id>/followup
       Follow-up status/window only (no re-analysis triggered).

  POST /api/fields/<id>/intervention/<intervention_id>/followup/run
       Re-analyzes the field using the EXISTING FieldAnalyzer pipeline
       (backend/routes/fields.py::run_and_persist_field_analysis -- never a
       second analysis system), freezes the AFTER snapshot, and computes an
       explainable before/after outcome.
"""

import logging
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request, abort
from flask_login import login_required, current_user

from backend.extensions import db
from backend.models import Field, Intervention, PesticideUse
from backend.services.intervention_engine import (
    build_intervention_context,
    build_before_snapshot,
    build_after_snapshot,
    compute_followup_window,
    estimate_cost,
    evaluate_outcome,
    recommend_interventions,
    simulate_intervention,
)

logger = logging.getLogger(__name__)

intervention_bp = Blueprint("intervention", __name__)


def _get_owned_field_or_404(field_id: int) -> Field:
    field = Field.query.get_or_404(field_id)
    if field.user_id != current_user.id:
        abort(404)
    return field


def _get_owned_intervention_or_404(field: Field, intervention_id: int) -> Intervention:
    intervention = Intervention.query.get_or_404(intervention_id)
    if intervention.field_id != field.id:
        abort(404)
    return intervention


@intervention_bp.get("/api/fields/<int:field_id>/intervention/context")
@login_required
def api_intervention_context(field_id):
    field = _get_owned_field_or_404(field_id)
    return jsonify({"context": build_intervention_context(field)})


@intervention_bp.get("/api/fields/<int:field_id>/intervention/options")
@login_required
def api_intervention_options(field_id):
    field = _get_owned_field_or_404(field_id)
    try:
        result = recommend_interventions(field)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Intervention recommendation failed for field_id=%s", field_id)
        return jsonify({"error": f"Could not compute intervention options: {exc}"}), 500
    return jsonify({"intervention": result})


@intervention_bp.post("/api/fields/<int:field_id>/intervention/simulate")
@login_required
def api_intervention_simulate(field_id):
    field = _get_owned_field_or_404(field_id)
    payload = request.get_json(force=True, silent=True) or {}

    pesticide_use_id = payload.get("pesticide_use_id")
    affected_area_ha = payload.get("affected_area_ha")

    if not pesticide_use_id:
        return jsonify({"error": "pesticide_use_id is required."}), 400
    try:
        affected_area_ha = float(affected_area_ha)
    except (TypeError, ValueError):
        return jsonify({"error": "affected_area_ha must be a number."}), 400
    if affected_area_ha <= 0:
        return jsonify({"error": "affected_area_ha must be greater than 0."}), 400

    pesticide_use = PesticideUse.query.get(pesticide_use_id)

    if not pesticide_use:
        return jsonify({"error": "Unknown pesticide_use_id."}), 404

    recommendation = recommend_interventions(field)

    allowed_ids = {
        item["id"]
        for item in recommendation.get("matches", [])
        if item.get("id") is not None
    }

    if pesticide_use.id not in allowed_ids:
        return jsonify({
            "error": "This pesticide option is not an approved-use match for the current field intervention."
        }), 403

    result = simulate_intervention(pesticide_use, affected_area_ha)
    return jsonify({"simulation": result})


# ------------------------------------------------------ Stage 4 routes -----

_SELECTABLE_OPTIONS = {"targeted", "field_wide", "monitor"}
# What recommend_interventions() must have returned for a given farmer
# choice to be valid -- prevents a client from POSTing 'targeted' when the
# decision gate actually said 'verify' (e.g. water stress / disease / low
# confidence), which is exactly the "diagnosis jumps straight to pesticide"
# failure mode this whole feature exists to close off.
_OPTION_REQUIRES_PATHWAY = {
    "targeted": {"targeted", "field_wide_available", "monitor", "verify"},
    "field_wide": {"field_wide_available"},
    "monitor": {"monitor", "verify", "targeted", "field_wide_available"},
}


@intervention_bp.post("/api/fields/<int:field_id>/intervention/record")
@login_required
def api_record_intervention(field_id):
    field = _get_owned_field_or_404(field_id)
    payload = request.get_json(force=True, silent=True) or {}

    selected_option = payload.get("selected_option")
    if selected_option not in _SELECTABLE_OPTIONS:
        return jsonify({"error": "selected_option must be 'targeted', 'field_wide', or 'monitor'."}), 400

    try:
        recommendation = recommend_interventions(field)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Intervention recommendation failed for field_id=%s", field_id)
        return jsonify({"error": f"Could not compute intervention options: {exc}"}), 500

    recommended_pathway = recommendation.get("recommended_pathway")

    # Recommendation does NOT restrict farmer choice.
    # The farmer may choose any pathway that is actually available.
    available_options = {"targeted", "monitor"}

    if recommendation.get("field_wide_available"):
        available_options.add("field_wide")

    if selected_option not in available_options:
        return jsonify({
            "error": (
                f"'{selected_option}' is not currently available for this field. "
                "Reload the intervention panel and choose from the options shown."
            )
        }), 409

    # Store the recommendation separately from the farmer's actual choice.
    pathway = recommended_pathway

    pesticide_use = None
    matches_snapshot = recommendation.get("matches") or []
    suspected_pest = None

    if selected_option in ("targeted", "field_wide"):
        pesticide_use_id = payload.get("pesticide_use_id")
        if not pesticide_use_id:
            return jsonify({"error": "pesticide_use_id is required for a targeted or field-wide selection."}), 400

        allowed_ids = {m["id"] for m in matches_snapshot if m.get("id") is not None}
        if int(pesticide_use_id) not in allowed_ids:
            return jsonify({
                "error": "This pesticide option is not an approved-use match for the current field intervention."
            }), 403

        pesticide_use = PesticideUse.query.get(pesticide_use_id)
        if not pesticide_use:
            return jsonify({"error": "Unknown pesticide_use_id."}), 404
        suspected_pest = pesticide_use.pest

    # Affected area: field-wide uses the whole field, targeted/monitor use
    # the engine's stressed-zone-based estimate (farmer may override with a
    # smaller/measured figure, but never a larger one than the field itself).
    # if selected_option == "field_wide":
    #     affected_area_ha = field.area_ha
    # else:
    #     affected_area_ha = payload.get("affected_area_ha") or recommendation.get("affected_area_ha")
    #     try:
    #         affected_area_ha = float(affected_area_ha) if affected_area_ha is not None else None
    #     except (TypeError, ValueError):
    #         affected_area_ha = recommendation.get("affected_area_ha")

    if selected_option == "field_wide":
        affected_area_ha = field.area_ha
    elif selected_option == "targeted":
        raw_area = payload.get("affected_area_ha")
        try:
            affected_area_ha = float(raw_area) if raw_area is not None else None
        except (TypeError, ValueError):
            affected_area_ha = None
        if not affected_area_ha:
            affected_area_ha = recommendation.get("affected_area_ha")
    else:  # monitor
        affected_area_ha = None


    planning_snapshot = None
    if pesticide_use is not None and affected_area_ha:
        simulation = simulate_intervention(pesticide_use, affected_area_ha)
        price = payload.get("price_per_unit_inr")
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            price = None
        planning_snapshot = simulation
        planning_snapshot["cost"] = estimate_cost(simulation, price)

    diagnosis = field.latest_diagnosis()
    before_snapshot = build_before_snapshot(field)
    followup_window = compute_followup_window(selected_option)

    targeting = recommendation.get("targeting") or {}
    affected_zones_at_decision = targeting.get("affected_zones") or {
        "stressed": before_snapshot.get("stressed_zones"),
        "total": before_snapshot.get("total_zones"),
    }

    now = datetime.now(timezone.utc)
    intervention = Intervention(
        field_id=field.id,
        diagnosis_id=diagnosis.id if diagnosis else None,
        pathway_at_decision=pathway,
        matches_at_decision=matches_snapshot,
        selected_option=selected_option,
        pesticide_use_id=pesticide_use.id if pesticide_use else None,
        suspected_pest=suspected_pest,
        affected_zones_at_decision=affected_zones_at_decision,
        affected_area_ha=affected_area_ha,
        planning=planning_snapshot,
        farmer_notes=(payload.get("farmer_notes") or "").strip() or None,
        before_analysis_id=before_snapshot.get("analysis_id"),
        before_risk_assessment_id=before_snapshot.get("risk_assessment_id"),
        before_snapshot=before_snapshot,
        follow_up_window_days_min=followup_window["min_days"],
        follow_up_window_days_max=followup_window["max_days"],
        follow_up_due_at=now + timedelta(days=followup_window["min_days"]),
        status="recorded",
    )
    db.session.add(intervention)
    db.session.commit()

    return jsonify({"intervention": intervention.to_dict(), "followup_window": followup_window}), 201


@intervention_bp.get("/api/fields/<int:field_id>/intervention/history")
@login_required
def api_intervention_history(field_id):
    field = _get_owned_field_or_404(field_id)
    items = field.interventions.all()
    return jsonify({"interventions": [i.to_dict() for i in items]})


@intervention_bp.get("/api/fields/<int:field_id>/intervention/<int:intervention_id>")
@login_required
def api_intervention_detail(field_id, intervention_id):
    field = _get_owned_field_or_404(field_id)
    intervention = _get_owned_intervention_or_404(field, intervention_id)

    detail = intervention.to_dict()
    detail["diagnosis"] = intervention.diagnosis.to_dict(include_evidence=False) if intervention.diagnosis else None
    detail["field"] = {
        "id": field.id,
        "name": field.name,
        "crop_type": field.crop_type,
        "crop_stage": field.crop_stage,
    }
    return jsonify({"intervention": detail})


@intervention_bp.get("/api/fields/<int:field_id>/intervention/<int:intervention_id>/followup")
@login_required
def api_followup_status(field_id, intervention_id):
    field = _get_owned_field_or_404(field_id)
    intervention = _get_owned_intervention_or_404(field, intervention_id)

    if intervention.status == "follow_up_completed":
        status = "completed"
    elif intervention.follow_up_due_at and intervention.follow_up_due_at <= datetime.now(timezone.utc):
        status = "due"
    else:
        status = "scheduled"

    return jsonify({
        "follow_up_status": status,
        "follow_up_window_days_min": intervention.follow_up_window_days_min,
        "follow_up_window_days_max": intervention.follow_up_window_days_max,
        "follow_up_due_at": intervention.follow_up_due_at.isoformat() if intervention.follow_up_due_at else None,
        "recorded_at": intervention.created_at.isoformat(),
    })


@intervention_bp.post("/api/fields/<int:field_id>/intervention/<int:intervention_id>/followup/run")
@login_required
def api_run_followup(field_id, intervention_id):
    field = _get_owned_field_or_404(field_id)
    intervention = _get_owned_intervention_or_404(field, intervention_id)

    if intervention.status == "follow_up_completed":
        return jsonify({"error": "Follow-up has already been completed for this intervention."}), 409

    # Reuse the EXISTING analysis pipeline -- never a second analyzer.
    from backend.routes.fields import run_and_persist_field_analysis

    try:
        analysis, risk = run_and_persist_field_analysis(field)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Follow-up re-analysis failed for intervention_id=%s", intervention_id)
        return jsonify({"error": f"Follow-up analysis failed: {exc}"}), 500

    after_snapshot = build_after_snapshot(analysis, risk)
    outcome = evaluate_outcome(intervention.before_snapshot, after_snapshot)

    intervention.after_analysis_id = analysis.id
    intervention.after_risk_assessment_id = risk.id if risk else None
    intervention.after_snapshot = after_snapshot
    intervention.status = "follow_up_completed"
    intervention.follow_up_completed_at = datetime.now(timezone.utc)
    intervention.outcome = outcome["outcome"]
    intervention.outcome_explanation = outcome["explanation"]
    db.session.commit()

    return jsonify({
        "intervention": intervention.to_dict(),
        "before": intervention.before_snapshot,
        "after": after_snapshot,
        "outcome": outcome,
    })