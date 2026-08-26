"""
Fields blueprint
-----------------
Owns the persistent farmer workflow: dashboard -> add field -> save field
-> open field -> analyze/refresh -> view zone results -> back to dashboard.

Every query here is scoped to `current_user.id`, and `_get_owned_field_or_404`
is the single choke point that enforces "a farmer can only access their own
fields" -- it is used by every field-scoped route below, so a farmer can
never reach another farmer's field just by changing the :field_id in the
URL/API call.

The scientific pipeline itself (SatelliteService -> spectral_analysis ->
zone_processor -> HyperspectralService) is untouched -- `analyze_field()`
from field_analyzer.py is called exactly as before. This module's only job
is to load the saved polygon, call that existing pipeline, and persist the
result as a new Analysis + ZoneResult rows (never overwriting history).
"""

import logging

from flask import Blueprint, jsonify, render_template, request, abort
from flask_login import login_required, current_user

from backend.extensions import db
from backend.models import Field, Analysis, ZoneResult
from backend.services.field_analyzer import analyze_field
from backend.services.zone_processor import compute_field_area_ha
from backend.services.weather_service import WeatherService, fetch_and_store_weather

logger = logging.getLogger(__name__)

fields_bp = Blueprint("fields", __name__)


def _get_owned_field_or_404(field_id: int) -> Field:
    field = Field.query.get_or_404(field_id)
    if field.user_id != current_user.id:
        # 404 (not 403) so a farmer probing other IDs learns nothing about
        # whether the field exists at all.
        abort(404)
    return field


# ---------------------------------------------------------------- pages ---
@fields_bp.get("/dashboard")
@login_required
def dashboard_page():
    return render_template("dashboard.html")


@fields_bp.get("/fields/new")
@login_required
def add_field_page():
    return render_template("add_field.html")


@fields_bp.get("/fields/<int:field_id>")
@login_required
def field_detail_page(field_id):
    field = _get_owned_field_or_404(field_id)
    return render_template("field_detail.html", field=field)


# --------------------------------------------------------------- API ------
@fields_bp.get("/api/fields")
@login_required
def api_list_fields():
    fields = (
        Field.query.filter_by(user_id=current_user.id)
        .order_by(Field.created_at.desc())
        .all()
    )
    result = []
    for f in fields:
        data = f.to_dict(include_latest_analysis=True, include_latest_weather=True)
        # Simple, rule-based monitoring status for the dashboard card --
        # not a prediction, just a summary of the latest weather reading.
        data["weather_status"] = WeatherService.summarize_status(data.get("latest_weather"))
        result.append(data)
    return jsonify({"fields": result})


@fields_bp.post("/api/fields")
@login_required
def api_create_field():
    payload = request.get_json(force=True, silent=True) or {}

    name = (payload.get("name") or "").strip()
    polygon = payload.get("polygon")
    crop_type = payload.get("crop_type")
    crop_stage = payload.get("crop_stage")

    if not name:
        return jsonify({"error": "Field name is required."}), 400
    if not polygon or len(polygon) < 3:
        return jsonify({"error": "A valid field polygon (>=3 points) is required."}), 400
    if not crop_type or not crop_stage:
        return jsonify({"error": "crop_type and crop_stage are required."}), 400

    if polygon[0] != polygon[-1]:
        polygon = polygon + [polygon[0]]

    try:
        area_ha = round(compute_field_area_ha(polygon), 3)
    except Exception:
        area_ha = None

    field = Field(
        user_id=current_user.id,
        name=name,
        polygon=polygon,
        crop_type=crop_type,
        crop_stage=crop_stage,
        area_ha=area_ha,
    )
    db.session.add(field)
    db.session.commit()

    # Fetch the first weather observation right away so the farmer doesn't
    # have to wait for the next periodic background run to see anything on
    # the dashboard / field detail page. Best-effort: a saved field must
    # never be lost just because the weather API had a hiccup -- the
    # periodic job will pick it up on the next cycle either way.
    try:
        fetch_and_store_weather(field)
    except Exception:  # noqa: BLE001
        logger.exception("Initial weather fetch failed for field_id=%s", field.id)

    return jsonify({"field": field.to_dict()}), 201


@fields_bp.get("/api/fields/<int:field_id>")
@login_required
def api_get_field(field_id):
    field = _get_owned_field_or_404(field_id)
    data = field.to_dict()
    latest = field.latest_analysis()
    data["latest_analysis"] = latest.to_dict(include_zones=True) if latest else None
    latest_weather = field.latest_weather()
    data["latest_weather"] = latest_weather.to_dict() if latest_weather else None
    return jsonify({"field": data})


@fields_bp.delete("/api/fields/<int:field_id>")
@login_required
def api_delete_field(field_id):
    field = _get_owned_field_or_404(field_id)
    db.session.delete(field)  # cascades to analyses -> zone_results
    db.session.commit()
    return jsonify({"ok": True})


@fields_bp.post("/api/fields/<int:field_id>/analyze")
@login_required
def api_analyze_field(field_id):
    field = _get_owned_field_or_404(field_id)

    try:
        result = analyze_field(field.polygon, field.crop_type, field.crop_stage)
    except Exception as exc:  # noqa: BLE001 - surface a clean error to the UI
        return jsonify({"error": f"Analysis failed: {exc}"}), 500

    summary = result["summary"]
    zones = result["zones"]

    analysis = Analysis(
        field_id=field.id,
        crop_type_snapshot=field.crop_type,
        crop_stage_snapshot=field.crop_stage,
        observation_date=summary["observation_date"],
        data_source=result["data_source"],
        analyzed_area_ha=summary["analyzed_area_ha"],
        overall_condition=summary["overall_condition"],
        mean_ndvi=summary["mean_ndvi"],
        zone_healthy_count=summary["zone_stats"]["healthy"],
        zone_moderate_count=summary["zone_stats"]["moderate"],
        zone_stressed_count=summary["zone_stats"]["stressed"],
        zone_total_count=summary["zone_stats"]["total"],
    )
    db.session.add(analysis)
    db.session.flush()  # assign analysis.id before creating child rows

    for zone in zones:
        hyper = zone.get("hyperspectral") or {}
        db.session.add(
            ZoneResult(
                analysis_id=analysis.id,
                zone_id=zone["zone_id"],
                geometry=zone["geometry"],
                area_ha=zone["area_ha"],
                ndvi=zone["ndvi"],
                ndre=zone.get("ndre"),
                savi=zone.get("savi"),
                ndmi=zone.get("ndmi"),
                health_status=zone["health_status"],
                hyperspectral_confidence=hyper.get("confidence_pct"),
                hyperspectral_verified=hyper.get("verified"),
            )
        )

    # Keep the field's area_ha in sync with the latest real satellite-based
    # measurement (it was only an estimate at save time).
    field.area_ha = summary["analyzed_area_ha"]

    db.session.commit()

    return jsonify({"analysis": analysis.to_dict(include_zones=True)})


@fields_bp.get("/api/fields/<int:field_id>/analyses")
@login_required
def api_field_analysis_history(field_id):
    field = _get_owned_field_or_404(field_id)
    analyses = field.analyses.all()  # already newest-first
    return jsonify(
        {"analyses": [a.to_dict(include_zones=False) for a in analyses]}
    )


@fields_bp.get("/api/fields/<int:field_id>/analyses/<int:analysis_id>")
@login_required
def api_get_analysis(field_id, analysis_id):
    field = _get_owned_field_or_404(field_id)
    analysis = Analysis.query.get_or_404(analysis_id)
    if analysis.field_id != field.id:
        abort(404)
    return jsonify({"analysis": analysis.to_dict(include_zones=True)})


# -------------------------------------------------------------- weather ---
@fields_bp.get("/api/fields/<int:field_id>/weather")
@login_required
def api_get_field_weather(field_id):
    """Latest weather observation for this field. If none exists yet
    (brand new field, or the initial fetch at save-time failed), fetch one
    now so the farmer never sees a permanently empty weather panel -- the
    background job then keeps it fresh going forward."""
    field = _get_owned_field_or_404(field_id)
    latest = field.latest_weather()
    if latest is None:
        try:
            latest = fetch_and_store_weather(field)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": f"Could not fetch weather: {exc}"}), 502

    data = latest.to_dict()
    data["status"] = WeatherService.summarize_status(data)
    return jsonify({"weather": data})


@fields_bp.post("/api/fields/<int:field_id>/weather/refresh")
@login_required
def api_refresh_field_weather(field_id):
    """Lets the farmer manually pull a fresh reading instead of waiting for
    the next periodic background cycle -- mirrors the existing
    'Analyze / Refresh Analysis' pattern used for satellite data."""
    field = _get_owned_field_or_404(field_id)
    try:
        observation = fetch_and_store_weather(field)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Could not refresh weather: {exc}"}), 502

    data = observation.to_dict()
    data["status"] = WeatherService.summarize_status(data)
    return jsonify({"weather": data})


@fields_bp.get("/api/fields/<int:field_id>/weather/history")
@login_required
def api_field_weather_history(field_id):
    field = _get_owned_field_or_404(field_id)
    limit = request.args.get("limit", default=24, type=int)
    observations = field.weather_observations.limit(limit).all()  # already newest-first
    return jsonify({"observations": [o.to_dict() for o in observations]})