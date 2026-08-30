"""
feature_engineering
--------------------
Turns everything currently known about a field -- the just-completed
satellite Analysis, weather history, IoT sensor history, and the field's
own analysis history -- into ONE flat, versioned dict: the "feature
snapshot" that gets persisted as a FeatureSnapshot row.

This is the foundation the prompt asked for: a stable, explainable feature
structure that a real ML model can later be trained/served against,
*without* this module needing to change. Today `risk_engine.py` consumes
this dict with hand-written rules; tomorrow a trained model can consume
the exact same dict instead.

Design choices
--------------
- Flat dict of name -> float/str/bool/None. Maps directly onto a single
  row of a tabular feature matrix (easy to dump many snapshots to a
  DataFrame / CSV for model training later).
- Every feature name is prefixed by source (`sat_`, `wx_`, `iot_`,
  `hist_`, `ctx_`) so it's obvious at a glance which data source produced
  it, and so a future model's feature-importance output is immediately
  interpretable to a non-ML person.
- Missing data produces `None` values (never a fabricated number) plus a
  companion `*_available` / `data_completeness` block, so risk_engine.py
  (and later, an ML model) can explicitly reason about confidence instead
  of silently treating "no sensor" the same as "sensor read zero".
- Pure function of its inputs (field, analysis, zones, weather rows,
  sensor rows) -- no DB writes here. routes/fields.py decides when to
  call it and persists the result as a FeatureSnapshot.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from backend.config import Config


def _avg(values: List[float]) -> Optional[float]:
    values = [v for v in values if v is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _parse_dt(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def build_feature_snapshot(field, analysis, zones: List[Dict], weather_rows: List, sensor_rows: List) -> Dict:
    """
    Parameters
    ----------
    field : backend.models.Field
    analysis : backend.models.Analysis   (just-created, current run)
    zones : list[dict]                    zone dicts as returned by
                                           field_analyzer.analyze_field()
                                           (has ndvi/ndre/savi/ndmi/health_status/area_ha)
    weather_rows : list[WeatherObservation]  newest-first, most recent
                                              Config.FEATURE_WEATHER_WINDOW rows
    sensor_rows : list[SensorReading]        newest-first, most recent
                                              Config.FEATURE_SENSOR_WINDOW rows

    Returns
    -------
    dict  (JSON-serializable, ready to store as FeatureSnapshot.features)
    """
    features: Dict = {}

    # ---------------------------------------------------------- context ---
    features["ctx_crop_type"] = field.crop_type
    features["ctx_crop_stage"] = field.crop_stage
    features["ctx_field_area_ha"] = field.area_ha

    # -------------------------------------------------- satellite (sat_) --
    total_area = sum(z.get("area_ha") or 0 for z in zones) or None
    ndvi_vals = [z.get("ndvi") for z in zones]
    ndre_vals = [z.get("ndre") for z in zones]
    savi_vals = [z.get("savi") for z in zones]
    ndmi_vals = [z.get("ndmi") for z in zones]

    stressed_area = sum(
        (z.get("area_ha") or 0) for z in zones if z.get("health_status") == "stressed"
    )
    moderate_area = sum(
        (z.get("area_ha") or 0) for z in zones if z.get("health_status") == "moderate"
    )

    features["sat_mean_ndvi"] = _avg(ndvi_vals)
    features["sat_mean_ndre"] = _avg(ndre_vals)
    features["sat_mean_savi"] = _avg(savi_vals)
    features["sat_mean_ndmi"] = _avg(ndmi_vals)
    features["sat_ndvi_min"] = min(ndvi_vals) if ndvi_vals else None
    features["sat_ndvi_max"] = max(ndvi_vals) if ndvi_vals else None
    features["sat_ndvi_stddev"] = _stddev(ndvi_vals)
    features["sat_zone_count"] = len(zones)
    features["sat_stressed_area_fraction"] = (
        round(stressed_area / total_area, 4) if total_area else None
    )
    features["sat_moderate_area_fraction"] = (
        round(moderate_area / total_area, 4) if total_area else None
    )
    stressed_zones = [
        z for z in zones
        if z.get("health_status") == "stressed"
    ]

    hyper_checked = sum(
        1 for z in stressed_zones
        if z.get("hyperspectral")
    )

    hyper_confirmed = sum(
        1 for z in stressed_zones
        if (z.get("hyperspectral") or {}).get("verified") is True
    )

    features["sat_hyperspectral_zones_checked"] = hyper_checked
    features["sat_hyperspectral_zones_confirmed_stress"] = hyper_confirmed

    # -------------------------------------------------- history (hist_) ---
    # Compare this analysis to the field's previous one (if any) to get a
    # trend, which matters more for "emerging" stress than a single reading.
    prev_analysis = None
    try:
        all_analyses = field.analyses.all()  # newest-first
        if len(all_analyses) >= 2:
            prev_analysis = all_analyses[1] if all_analyses[0].id == analysis.id else all_analyses[0]
    except Exception:
        prev_analysis = None

    features["hist_previous_analysis_available"] = prev_analysis is not None
    if prev_analysis is not None and prev_analysis.mean_ndvi is not None and features["sat_mean_ndvi"] is not None:
        features["hist_ndvi_delta"] = round(features["sat_mean_ndvi"] - prev_analysis.mean_ndvi, 4)
    else:
        features["hist_ndvi_delta"] = None

    prev_dt = _parse_dt(prev_analysis.created_at) if prev_analysis is not None else None
    cur_dt = _parse_dt(analysis.created_at) or datetime.now(timezone.utc)
    if prev_dt is not None:
        delta_days = abs((cur_dt - prev_dt).total_seconds()) / 86400.0
        features["hist_days_since_previous_analysis"] = round(delta_days, 2)
    else:
        features["hist_days_since_previous_analysis"] = None
    features["hist_total_analyses_count"] = field.analyses.count()

    # ----------------------------------------------------- weather (wx_) --
    temps = [w.temperature_c for w in weather_rows]
    humidities = [w.humidity_pct for w in weather_rows]
    winds = [w.wind_speed_kmh for w in weather_rows]
    precips = [w.precipitation_mm for w in weather_rows]

    features["wx_observations_used"] = len(weather_rows)
    features["wx_latest_temperature_c"] = weather_rows[0].temperature_c if weather_rows else None
    features["wx_latest_humidity_pct"] = weather_rows[0].humidity_pct if weather_rows else None
    features["wx_avg_temperature_c"] = _avg(temps)
    features["wx_avg_humidity_pct"] = _avg(humidities)
    features["wx_avg_wind_kmh"] = _avg(winds)
    features["wx_total_precip_mm"] = round(sum(p for p in precips if p is not None), 2) if precips else None
    features["wx_dry_reading_streak"] = _leading_zero_streak(precips)
    features["wx_heat_stress_readings"] = sum(1 for t in temps if t is not None and t >= 38)
    features["wx_cold_stress_readings"] = sum(1 for t in temps if t is not None and t <= 5)

    # --------------------------------------------------------- IoT (iot_) -
    soil_moist = [s.soil_moisture_pct for s in sensor_rows]
    soil_temp = [s.soil_temperature_c for s in sensor_rows]
    soil_ph = [s.soil_ph for s in sensor_rows]
    soil_ec = [s.soil_ec_ds_m for s in sensor_rows]
    leaf_wet = [s.leaf_wetness_pct for s in sensor_rows]

    features["iot_readings_used"] = len(sensor_rows)
    features["iot_has_real_device_data"] = any((s.source or "") != "simulated" for s in sensor_rows)
    features["iot_latest_soil_moisture_pct"] = sensor_rows[0].soil_moisture_pct if sensor_rows else None
    features["iot_avg_soil_moisture_pct"] = _avg(soil_moist)
    features["iot_avg_soil_temperature_c"] = _avg(soil_temp)
    features["iot_avg_soil_ph"] = _avg(soil_ph)
    features["iot_avg_soil_ec_ds_m"] = _avg(soil_ec)
    features["iot_avg_leaf_wetness_pct"] = _avg(leaf_wet)
    features["iot_low_moisture_readings"] = sum(1 for m in soil_moist if m is not None and m < 20)

    # Planned-sensor extension: N/P/K nutrients + on-site rain gauge. These
    # stay None (never a fabricated value) whenever the field has no
    # nutrient/rain-gauge probe reporting yet -- SensorReading columns are
    # nullable for exactly this reason. No existing consumer of the feature
    # dict is required to use these; the ML model (ml_risk_model.py) simply
    # was not trained on them and ignores them if present.
    soil_n = [s.soil_nitrogen_ppm for s in sensor_rows]
    soil_p = [s.soil_phosphorus_ppm for s in sensor_rows]
    soil_k = [s.soil_potassium_ppm for s in sensor_rows]
    rain_gauge = [s.rainfall_mm for s in sensor_rows]
    features["iot_avg_soil_nitrogen_ppm"] = _avg(soil_n)
    features["iot_avg_soil_phosphorus_ppm"] = _avg(soil_p)
    features["iot_avg_soil_potassium_ppm"] = _avg(soil_k)
    features["iot_total_rain_gauge_mm"] = (
        round(sum(r for r in rain_gauge if r is not None), 2) if any(r is not None for r in rain_gauge) else None
    )

    # ------------------------------------------------ data completeness ---
    # Explicit record of which sources actually contributed, so the risk
    # engine (and later, an ML model's input pipeline) can down-weight or
    # flag low-confidence snapshots instead of guessing.
    features["meta_feature_version"] = Config.FEATURE_SCHEMA_VERSION
    features["meta_generated_at"] = datetime.now(timezone.utc).isoformat()
    features["meta_sources_available"] = {
        "satellite": bool(zones),
        "weather": bool(weather_rows),
        "iot": bool(sensor_rows),
        "history": prev_analysis is not None,
    }

    return features


def _stddev(values: List[float]) -> Optional[float]:
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return round(variance ** 0.5, 4)


def _leading_zero_streak(precip_values_newest_first: List[Optional[float]]) -> int:
    """Counts how many of the most-recent readings (starting from the
    newest) had ~zero precipitation -- a simple dry-spell length proxy."""
    streak = 0
    for p in precip_values_newest_first:
        if p is None:
            break
        if p > 0.1:
            break
        streak += 1
    return streak