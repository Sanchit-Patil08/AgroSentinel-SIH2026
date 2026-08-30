"""
Diagnosis blueprint
--------------------
"Diagnose This Field" -> Stage 2 of the field-intelligence flow (see
backend/services/diagnosis_engine.py for the full picture).

Everything here hangs off the existing field-scoped API namespace
(/api/fields/<field_id>/diagnosis/...) and reuses the same ownership
choke point as fields.py (_get_owned_field_or_404) so a farmer can only
ever reach their own fields' diagnoses. Nothing in this module touches
the satellite/weather/IoT pipeline or the existing Analysis/RiskAssessment
routes -- it only reads from them.

Routes:
  GET  /api/fields/<id>/diagnosis/context
       Read-only context for the "why are we asking for inspection"
       screen -- crop/zone/risk/weather, no DB write.

  POST /api/fields/<id>/diagnosis
       Starts a new diagnosis episode (a new FieldDiagnosis row -- past
       diagnoses are never overwritten). body: {"inspection_method":
       "manual"|"drone"}. 'drone' is accepted by the schema for the
       future integration point described in the brief, but the actual
       drone workflow isn't implemented yet, so it responds 501.

  POST /api/fields/<id>/diagnosis/<diag_id>/evidence
       multipart/form-data evidence upload: file field "image" +
       optional "image_type", "damage_pattern", "note". Called once per
       photo; the farmer can upload several.

  POST /api/fields/<id>/diagnosis/<diag_id>/run
       Runs the rule-based diagnosis engine against whatever evidence has
       been uploaded so far (may be zero) and persists the result.

  GET  /api/fields/<id>/diagnosis
       Diagnosis history for the field, newest first (evidence omitted --
       just a count -- to keep the list endpoint light).

  GET  /api/fields/<id>/diagnosis/<diag_id>
       Full diagnosis detail including evidence metadata + URLs.

  GET  /api/fields/<id>/diagnosis/<diag_id>/evidence/<evidence_id>/file
       Serves one evidence image's bytes, ownership-checked.
"""

import logging
import os
import uuid

from flask import Blueprint, jsonify, request, abort, send_from_directory
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from backend.config import Config
from backend.extensions import db
from backend.models import (
    Field,
    FieldDiagnosis,
    DiagnosisEvidence,
    Analysis,
    ZoneResult,
    PesticideUse,
)
from backend.services.diagnosis_engine import build_diagnosis_context, run_diagnosis
from backend.services.intervention_engine import (
    recommend_interventions,
    simulate_intervention,
)

logger = logging.getLogger(__name__)

diagnosis_bp = Blueprint("diagnosis", __name__)

VALID_IMAGE_TYPES = {"leaf", "pest_insect", "closeup", "beneficial_insect", "other"}
VALID_DAMAGE_PATTERNS = {
    "chewing", "sucking", "curling", "wilting", "leaf_spot", "discoloration", "not_sure",
}


def _get_owned_field_or_404(field_id: int) -> Field:
    field = Field.query.get_or_404(field_id)
    if field.user_id != current_user.id:
        abort(404)
    return field


def _get_diagnosis_or_404(field: Field, diagnosis_id: int) -> FieldDiagnosis:
    diagnosis = FieldDiagnosis.query.get_or_404(diagnosis_id)
    if diagnosis.field_id != field.id:
        abort(404)
    return diagnosis


def _allowed_file(filename: str) -> bool:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in Config.DIAGNOSIS_ALLOWED_EXTENSIONS


# -------------------------------------------------------------- context ---
@diagnosis_bp.get("/api/fields/<int:field_id>/diagnosis/context")
@login_required
def api_diagnosis_context(field_id):
    field = _get_owned_field_or_404(field_id)
    return jsonify({"context": build_diagnosis_context(field)})


# ------------------------------------------------------------- create -----
@diagnosis_bp.post("/api/fields/<int:field_id>/diagnosis")
@login_required
def api_create_diagnosis(field_id):
    field = _get_owned_field_or_404(field_id)
    payload = request.get_json(force=True, silent=True) or {}
    method = (payload.get("inspection_method") or "manual").strip().lower()

    if method not in ("manual", "drone"):
        return jsonify({"error": "inspection_method must be 'manual' or 'drone'."}), 400

    if method == "drone":
        # Architecture is in place (inspection_method, the future
        # RGB/NoIR/thermal evidence shape) but drone hardware/control is
        # explicitly out of scope for this build -- see project brief.
        return jsonify({
            "error": "Drone inspection isn't available yet. The field data model already "
                     "supports it for a future release -- for now, choose manual inspection.",
        }), 501

    context = build_diagnosis_context(field)
    analysis = field.latest_analysis()
    risk = field.latest_risk_assessment()

    diagnosis = FieldDiagnosis(
        field_id=field.id,
        analysis_id=analysis.id if analysis else None,
        risk_assessment_id=risk.id if risk else None,
        inspection_method="manual",
        status="awaiting_evidence",
        context_snapshot=context,
        priority_zones=context.get("priority_zones"),
    )
    db.session.add(diagnosis)
    db.session.commit()

    return jsonify({"diagnosis": diagnosis.to_dict()}), 201


# ------------------------------------------------------------- evidence ---
@diagnosis_bp.post("/api/fields/<int:field_id>/diagnosis/<int:diagnosis_id>/evidence")
@login_required
def api_upload_evidence(field_id, diagnosis_id):
    field = _get_owned_field_or_404(field_id)
    diagnosis = _get_diagnosis_or_404(field, diagnosis_id)

    if "image" not in request.files:
        return jsonify({"error": "An 'image' file is required."}), 400

    file = request.files["image"]

    if not file or file.filename == "":
        return jsonify({"error": "An 'image' file is required."}), 400

    if not _allowed_file(file.filename):
        return jsonify({
            "error": f"Unsupported file type. Allowed: {', '.join(sorted(Config.DIAGNOSIS_ALLOWED_EXTENSIONS))}",
        }), 400

    analysis_id_raw = request.form.get("analysis_id")
    zone_id_raw = request.form.get("zone_id")

    if not analysis_id_raw:
        return jsonify({"error": "analysis_id is required."}), 400

    if not zone_id_raw:
        return jsonify({"error": "zone_id is required."}), 400

    try:
        analysis_id = int(analysis_id_raw)
        zone_id = int(zone_id_raw)
    except (TypeError, ValueError):
        return jsonify({
            "error": "analysis_id and zone_id must be integers."
        }), 400

    analysis = Analysis.query.filter_by(
        id=analysis_id,
        field_id=field.id
    ).first()

    if not analysis:
        return jsonify({
            "error": "The specified analysis does not belong to this field."
        }), 400

    zone = ZoneResult.query.filter_by(
        analysis_id=analysis.id,
        zone_id=zone_id
    ).first()

    if not zone:
        return jsonify({
            "error": "The specified zone does not exist in this analysis."
        }), 400

    image_type = (request.form.get("image_type") or "other").strip().lower()

    if image_type not in VALID_IMAGE_TYPES:
        image_type = "other"

    damage_pattern = (
        request.form.get("damage_pattern") or ""
    ).strip().lower() or None

    if damage_pattern and damage_pattern not in VALID_DAMAGE_PATTERNS:
        damage_pattern = None

    note = (request.form.get("note") or "").strip() or None

    diag_dir = os.path.join(
        Config.DIAGNOSIS_UPLOAD_DIR,
        str(diagnosis.id)
    )
    os.makedirs(diag_dir, exist_ok=True)

    ext = file.filename.rsplit(".", 1)[-1].lower()
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    stored_path = os.path.join(diag_dir, stored_name)

    file.save(stored_path)

    evidence = DiagnosisEvidence(
        diagnosis_id=diagnosis.id,
        analysis_id=analysis.id,
        zone_id=zone.zone_id,
        image_type=image_type,
        damage_pattern=damage_pattern,
        note=note,
        file_path=os.path.join(str(diagnosis.id), stored_name),
        original_filename=secure_filename(file.filename),
        content_type=file.mimetype,
    )

    db.session.add(evidence)
    db.session.commit()

    return jsonify({"evidence": evidence.to_dict()}), 201

    
@diagnosis_bp.get(
    "/api/fields/<int:field_id>/diagnosis/<int:diagnosis_id>/evidence/<int:evidence_id>/file"
)
@login_required
def api_get_evidence_file(field_id, diagnosis_id, evidence_id):
    field = _get_owned_field_or_404(field_id)
    diagnosis = _get_diagnosis_or_404(field, diagnosis_id)
    evidence = DiagnosisEvidence.query.get_or_404(evidence_id)
    if evidence.diagnosis_id != diagnosis.id:
        abort(404)

    directory = os.path.join(Config.DIAGNOSIS_UPLOAD_DIR, str(diagnosis.id))
    filename = os.path.basename(evidence.file_path)
    return send_from_directory(directory, filename, mimetype=evidence.content_type)


# ------------------------------------------------------------------ run ---
@diagnosis_bp.post("/api/fields/<int:field_id>/diagnosis/<int:diagnosis_id>/run")
@login_required
def api_run_diagnosis(field_id, diagnosis_id):
    field = _get_owned_field_or_404(field_id)
    diagnosis = _get_diagnosis_or_404(field, diagnosis_id)

    payload = request.get_json(force=True, silent=True) or {}
    notes = (payload.get("farmer_notes") or "").strip()
    if notes:
        diagnosis.farmer_notes = notes

    try:
        result = run_diagnosis(diagnosis)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Diagnosis run failed for diagnosis_id=%s", diagnosis.id)
        return jsonify({"error": f"Diagnosis failed: {exc}"}), 500

    diagnosis.possible_cause = result["possible_cause"]
    diagnosis.confidence_level = result["confidence_level"]
    diagnosis.confidence_score = result["confidence_score"]
    diagnosis.supporting_evidence = result["supporting_evidence"]
    diagnosis.recommended_verification = result["recommended_verification"]
    diagnosis.recommended_intervention = result["recommended_intervention"]
    diagnosis.protection_alert = result["protection_alert"]
    diagnosis.pest_detection = result.get("pest_detection") or []
    diagnosis.disease_detection = result.get("disease_detection") or []
    diagnosis.status = "diagnosed"

    db.session.commit()
    return jsonify({"diagnosis": diagnosis.to_dict()})


# --------------------------------------------------------------- history --
@diagnosis_bp.get("/api/fields/<int:field_id>/diagnosis")
@login_required
def api_list_diagnoses(field_id):
    field = _get_owned_field_or_404(field_id)
    diagnoses = field.diagnoses.all()  # already newest-first
    return jsonify({"diagnoses": [d.to_dict(include_evidence=False) for d in diagnoses]})


@diagnosis_bp.get("/api/fields/<int:field_id>/diagnosis/<int:diagnosis_id>")
@login_required
def api_get_diagnosis(field_id, diagnosis_id):
    field = _get_owned_field_or_404(field_id)
    diagnosis = _get_diagnosis_or_404(field, diagnosis_id)
    return jsonify({"diagnosis": diagnosis.to_dict(include_evidence=True)})

@diagnosis_bp.get("/api/fields/<int:field_id>/intervention")
@login_required
def api_intervention(field_id):
    field = _get_owned_field_or_404(field_id)

    try:
        result = recommend_interventions(field)
        return jsonify({"intervention": result})
    except Exception as exc:
        logger.exception(
            "Intervention recommendation failed for field_id=%s",
            field.id
        )
        return jsonify({
            "error": f"Intervention recommendation failed: {exc}"
        }), 500

@diagnosis_bp.post("/api/fields/<int:field_id>/intervention/simulate")
@login_required
def api_simulate_intervention(field_id):
    field = _get_owned_field_or_404(field_id)

    payload = request.get_json(force=True, silent=True) or {}

    pesticide_use_id = payload.get("pesticide_use_id")
    affected_area_ha = payload.get("affected_area_ha")

    if not pesticide_use_id:
        return jsonify({
            "error": "pesticide_use_id is required."
        }), 400

    try:
        affected_area_ha = float(affected_area_ha)
    except (TypeError, ValueError):
        return jsonify({
            "error": "affected_area_ha must be a number."
        }), 400

    if affected_area_ha <= 0:
        return jsonify({
            "error": "affected_area_ha must be greater than zero."
        }), 400

    try:
        pesticide_use_id = int(pesticide_use_id)
    except (TypeError, ValueError):
        return jsonify({
            "error": "Invalid pesticide_use_id."
        }), 400

    intervention = recommend_interventions(field)

    allowed_ids = {
        item["id"]
        for item in intervention.get("matches", [])
        if item.get("id") is not None
    }

    if pesticide_use_id not in allowed_ids:
        return jsonify({
            "error": (
                "Selected pesticide record is not one of the "
                "approved-use matches for this field's current "
                "intervention context."
            )
        }), 400

    pesticide_use = PesticideUse.query.get(pesticide_use_id)

    if not pesticide_use:
        return jsonify({
            "error": "Approved-use pesticide record not found."
        }), 404

    result = simulate_intervention(
        pesticide_use,
        affected_area_ha
    )

    return jsonify({
        "simulation": result
    })