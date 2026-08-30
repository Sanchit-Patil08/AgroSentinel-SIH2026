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
"""

import logging

from flask import Blueprint, jsonify, request, abort
from flask_login import login_required, current_user

from backend.models import Field, PesticideUse
from backend.services.intervention_engine import (
    build_intervention_context,
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