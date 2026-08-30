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

from backend.config import Config
from backend.extensions import db
from backend.models import Field, Analysis, ZoneResult, FeatureSnapshot, RiskAssessment
from backend.services.field_analyzer import analyze_field
from backend.services.zone_processor import compute_field_area_ha
from backend.services.weather_service import WeatherService, fetch_and_store_weather
from backend.services.iot_service import iot_service
from backend.services.feature_engineering import build_feature_snapshot
from backend.services.risk_engine import assess_risk
from backend.services import ml_risk_model

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
        latest_risk = f.latest_risk_assessment()
        data["latest_risk"] = latest_risk.to_dict() if latest_risk else None
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

    # Intelligence layer: build a feature snapshot from satellite + weather
    # + IoT + history, then score it with the risk engine. Best-effort --
    # a farmer must still get their satellite analysis even if this step
    # has a problem, exactly like the initial-weather-fetch pattern above.
    risk_payload = None
    try:
        risk = _build_intelligence_layer(field, analysis, zones)
        if risk is not None:
            risk_payload = risk.to_dict()
    except Exception:  # noqa: BLE001
        logger.exception("Feature/risk pipeline failed for field_id=%s analysis_id=%s", field.id, analysis.id)

    response = analysis.to_dict(include_zones=True)
    response["risk"] = risk_payload
    return jsonify({"analysis": response})


def _build_intelligence_layer(field: Field, analysis: Analysis, zones: list) -> "RiskAssessment | None":
    """Builds and persists one FeatureSnapshot + RiskAssessment tied to
    `analysis`. Pulls in the most recent weather/IoT history (ensuring at
    least one IoT reading exists, simulating one if the field has no real
    sensors yet -- see iot_service.fetch_or_simulate_latest), then hands
    the resulting flat feature dict to BOTH the rule-based risk engine
    (causes + recommendations + confidence -- the decision/explanation
    layer, always used) and the ML stress model (a probability -- the
    prediction layer, see ml_risk_model.py).

    Status precedence (risk_level / risk_score = the field-level
    Healthy/Moderate/Stressed status):
      - ML model available and returns a usable prediction -> the ML
        prediction becomes risk_level/risk_score (status_source='ml_v1').
      - ML unavailable/disabled/failed -> falls back to the rule engine's
        risk_level/risk_score, unchanged from before (status_source=
        'rule_based_v1').
    causes/recommendations/confidence ALWAYS come from the rule engine,
    regardless of which engine won the status -- this function never lets
    the ML layer touch those.
    """
    try:
        iot_service.fetch_or_simulate_latest(field)
    except Exception:  # noqa: BLE001
        logger.exception("IoT fallback reading failed for field_id=%s", field.id)

    weather_rows = field.weather_observations.limit(Config.FEATURE_WEATHER_WINDOW).all()
    sensor_rows = field.sensor_readings.limit(Config.FEATURE_SENSOR_WINDOW).all()

    features = build_feature_snapshot(field, analysis, zones, weather_rows, sensor_rows)

    snapshot = FeatureSnapshot(
        field_id=field.id,
        analysis_id=analysis.id,
        feature_version=Config.FEATURE_SCHEMA_VERSION,
        features=features,
    )
    db.session.add(snapshot)
    db.session.flush()  # assign snapshot.id before linking it from RiskAssessment

    # Rule engine ALWAYS runs -- it remains the sole source of causes,
    # recommendations, confidence, and the fallback status.
    result = assess_risk(features)

    # ML prediction layer -- independent of, and computed alongside, the
    # rule-based result above. Best-effort: if the model isn't trained yet
    # or prediction fails for any reason, ml_risk_model.predict() already
    # returns a clean {"available": False, ...} dict rather than raising,
    # so this can never take down the (working) rule-based assessment.
    if Config.ML_STRESS_MODEL_ENABLED:
        try:
            ml_result = ml_risk_model.predict(features)
        except Exception:  # noqa: BLE001
            logger.exception("ML stress model prediction failed for field_id=%s", field.id)
            ml_result = {"available": False, "note": "ML prediction failed unexpectedly -- see server logs."}
    else:
        ml_result = {"available": False, "note": "ML stress model disabled (ML_STRESS_MODEL_ENABLED=0)."}

    # Status precedence: ML wins when it actually produced a usable
    # prediction; otherwise fall back to the rule engine's status exactly
    # as before. Never fabricated -- ml_result["available"] is only True
    # when ml_risk_model.py loaded a real trained artifact and scored
    # `features` with it (see that module for the full contract).
    ml_stress_probability = ml_result.get("stress_probability")
    if ml_result.get("available") and ml_stress_probability is not None:
        status_risk_level = ml_result["risk_level"]
        status_risk_score = ml_stress_probability
        status_source = "ml_v1"
    else:
        status_risk_level = result["risk_level"]
        status_risk_score = result["risk_score"]
        status_source = "rule_based_v1"

    risk = RiskAssessment(
        field_id=field.id,
        analysis_id=analysis.id,
        feature_snapshot_id=snapshot.id,
        risk_level=status_risk_level,
        risk_score=status_risk_score,
        confidence=result["confidence"],
        causes=result["causes"],
        recommendations=result["recommendations"],
        method=result["method"],
        status_source=status_source,
        ml_prediction=ml_result,
    )
    db.session.add(risk)
    db.session.commit()
    return risk


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


# ------------------------------------------------------------- IoT sensors -
@fields_bp.get("/api/fields/<int:field_id>/sensors")
@login_required
def api_get_field_sensors(field_id):
    """Latest sensor reading for this field. If no real device has ever
    reported in, a simulated reading is generated (mirrors how weather
    behaves for a brand-new field) so the panel is never permanently
    empty -- see backend/services/iot_service.py."""
    field = _get_owned_field_or_404(field_id)
    try:
        latest = iot_service.fetch_or_simulate_latest(field)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Could not fetch sensor data: {exc}"}), 502

    if latest is None:
        return jsonify({"reading": None})
    return jsonify({"reading": latest.to_dict()})


@fields_bp.post("/api/fields/<int:field_id>/sensors")
@login_required
def api_post_field_sensor_reading(field_id):
    """Ingests a real sensor reading (device push or manual entry).

    Accepts a JSON body with any subset of: soil_moisture_pct,
    soil_temperature_c, soil_ph, soil_ec_ds_m, leaf_wetness_pct,
    air_temperature_c, air_humidity_pct, light_lux, battery_pct, plus
    optional sensor_id, zone_label, latitude, longitude. Unrecognized
    extra keys are kept in raw_data rather than rejected, so a new device
    type doesn't need a code change to start sending data.
    """
    field = _get_owned_field_or_404(field_id)
    payload = request.get_json(force=True, silent=True) or {}

    known_keys = {
        "soil_moisture_pct", "soil_temperature_c", "soil_ph", "soil_ec_ds_m",
        "leaf_wetness_pct", "air_temperature_c", "air_humidity_pct",
        "light_lux", "battery_pct",
    }
    if not any(k in payload and payload[k] is not None for k in known_keys):
        return jsonify({"error": "At least one sensor reading value is required."}), 400

    payload.setdefault("source", "device")
    reading = iot_service.store_reading(field, payload)
    return jsonify({"reading": reading.to_dict()}), 201


@fields_bp.post("/api/fields/<int:field_id>/sensors/simulate")
@login_required
def api_simulate_field_sensor_reading(field_id):
    """Manually generates a fresh simulated reading -- the IoT equivalent
    of the 'Refresh Weather' button, useful for demoing/developing without
    real hardware deployed yet."""
    field = _get_owned_field_or_404(field_id)
    try:
        reading = iot_service.force_simulate(field)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Could not simulate a reading: {exc}"}), 502
    return jsonify({"reading": reading.to_dict()})


@fields_bp.get("/api/fields/<int:field_id>/sensors/history")
@login_required
def api_field_sensor_history(field_id):
    field = _get_owned_field_or_404(field_id)
    limit = request.args.get("limit", default=24, type=int)
    readings = field.sensor_readings.limit(limit).all()  # already newest-first
    return jsonify({"readings": [r.to_dict() for r in readings]})


# ------------------------------------------------------ stress / risk -----
@fields_bp.get("/api/fields/<int:field_id>/risk")
@login_required
def api_get_field_risk(field_id):
    """Latest stress/risk assessment for this field (produced alongside
    the most recent satellite analysis). Returns risk: null if the field
    has never been analyzed yet, rather than an error."""
    field = _get_owned_field_or_404(field_id)
    latest = field.latest_risk_assessment()
    return jsonify({"risk": latest.to_dict() if latest else None})


@fields_bp.get("/api/fields/<int:field_id>/risk/history")
@login_required
def api_field_risk_history(field_id):
    field = _get_owned_field_or_404(field_id)
    limit = request.args.get("limit", default=24, type=int)
    assessments = field.risk_assessments.limit(limit).all()  # already newest-first
    return jsonify({"assessments": [r.to_dict() for r in assessments]})


# ------------------------------------------------- ML feature snapshots ---
@fields_bp.get("/api/fields/<int:field_id>/features/latest")
@login_required
def api_get_latest_feature_snapshot(field_id):
    """Exposes the raw feature vector behind the latest risk assessment --
    intended for development/debugging and for exporting training data for
    the future ML model, not for the main farmer-facing UI."""
    field = _get_owned_field_or_404(field_id)
    latest = field.feature_snapshots.first()
    return jsonify({"feature_snapshot": latest.to_dict() if latest else None})