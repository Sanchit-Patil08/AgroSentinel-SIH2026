"""
IoTService
----------
Responsible for IoT sensor data for a field: storing readings pushed by
real devices, and -- when none exist yet -- generating a deterministic
SIMULATED reading so the sensor panel and the feature/risk pipeline always
have something to work with (same "sample vs live" pattern already used by
SatelliteService and WeatherService in this project).

Two ways a SensorReading row gets created:

  1. Real device / manual entry (`store_reading`):
     A device (or a farmer typing in a handheld meter value) POSTs to
     /api/fields/<id>/sensors. Any subset of the known fields can be sent;
     unspecified fields stay NULL. Once at least one real reading exists
     for a field, `latest_reading()` always prefers it over anything
     simulated.

  2. Simulated fallback (`fetch_or_simulate_latest`):
     Used by the sensor panel and by feature_engineering.py so a brand
     new field (no hardware deployed yet) still has plausible values to
     show/compute with. Deterministic per field + current hour (like
     WeatherService._fetch_sample) so it's stable within an hour rather
     than randomly jumping on every call, and is loosely correlated with
     the field's latest weather (hot/dry weather -> lower soil moisture)
     so the demo data tells a coherent story instead of being pure noise.

This module intentionally knows nothing about ML/risk scoring -- it only
owns "get/store sensor readings for a field". feature_engineering.py reads
from it.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

from backend.config import Config

logger = logging.getLogger(__name__)

SENSOR_FIELDS = (
    "soil_moisture_pct",
    "soil_temperature_c",
    "soil_ph",
    "soil_ec_ds_m",
    "leaf_wetness_pct",
    "air_temperature_c",
    "air_humidity_pct",
    "light_lux",
    "battery_pct",
    # Planned-sensor extension (soil nutrients + on-site rain gauge)
    "soil_nitrogen_ppm",
    "soil_phosphorus_ppm",
    "soil_potassium_ppm",
    "rainfall_mm",
)


class IoTService:
    def __init__(self, config: Config = Config):
        self.config = config

    # ------------------------------------------------------------------
    # Real / manual readings
    # ------------------------------------------------------------------
    def store_reading(self, field, payload: Dict) -> "SensorReading":  # noqa: F821
        """Persists one SensorReading row for `field` from a device/manual
        payload. Only known numeric sensor fields (SENSOR_FIELDS) plus
        sensor_id/zone_label/lat/lon are read out of `payload`; anything
        else in the payload is kept in `raw_data` for traceability without
        needing a schema change to support a new device's extra fields."""
        from backend.extensions import db
        from backend.models import SensorReading

        values = {}
        for key in SENSOR_FIELDS:
            if key in payload and payload[key] is not None:
                try:
                    values[key] = float(payload[key])
                except (TypeError, ValueError):
                    continue

        reading = SensorReading(
            field_id=field.id,
            sensor_id=(payload.get("sensor_id") or None),
            zone_label=(payload.get("zone_label") or None),
            latitude=payload.get("latitude"),
            longitude=payload.get("longitude"),
            source=payload.get("source") or "manual",
            raw_data=payload,
            **values,
        )
        db.session.add(reading)
        db.session.commit()
        return reading

    # ------------------------------------------------------------------
    # Simulated fallback
    # ------------------------------------------------------------------
    def simulate_reading(self, field) -> Dict:
        """Deterministic synthetic sensor snapshot so the app works with
        zero hardware. Seeded on field id + current hour (stable within an
        hour). Loosely reacts to the field's latest weather reading if one
        exists, so simulated soil moisture drops after hot/dry weather and
        rises after rain -- coherent demo data rather than pure noise."""
        hour_bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H")
        seed_str = f"field:{field.id}:{hour_bucket}"
        seed = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)

        base_moisture = 35 + (seed % 3000) / 100.0  # ~35-65%
        base_soil_temp = 20 + (seed // 7 % 1200) / 100.0  # ~20-32 C
        base_ph = 6.0 + (seed // 13 % 200) / 100.0  # ~6.0-8.0
        base_ec = 0.5 + (seed // 17 % 300) / 100.0  # ~0.5-3.5 dS/m
        base_leaf_wetness = (seed // 19 % 4000) / 100.0  # 0-40%
        base_light = 15000 + (seed // 23 % 70000)  # lux, daytime-ish sample
        battery = 55 + (seed // 29 % 4500) / 100.0  # 55-100%
        base_n = 20 + (seed // 31 % 8000) / 100.0    # ~20-100 ppm N
        base_p = 10 + (seed // 37 % 4000) / 100.0    # ~10-50 ppm P
        base_k = 15 + (seed // 41 % 12000) / 100.0   # ~15-135 ppm K
        base_rainfall = 0.0  # on-site rain gauge; only non-zero when it actually rained

        latest_weather = field.latest_weather() if hasattr(field, "latest_weather") else None
        air_temp = None
        air_humidity = None
        if latest_weather is not None:
            air_temp = latest_weather.temperature_c
            air_humidity = latest_weather.humidity_pct
            precip = latest_weather.precipitation_mm or 0
            if air_temp is not None and air_temp >= 32:
                base_moisture -= 8
                base_soil_temp += 2
            if precip and precip > 0:
                base_moisture += min(precip * 2, 20)
                base_leaf_wetness += min(precip * 5, 40)
                base_rainfall = precip  # rain gauge echoes the same recent-rain event
        else:
            air_temp = round(base_soil_temp + 2, 1)
            air_humidity = round(min(base_moisture + 15, 95), 1)

        base_moisture = max(5.0, min(base_moisture, 95.0))
        base_leaf_wetness = max(0.0, min(base_leaf_wetness, 100.0))

        return {
            "soil_moisture_pct": round(base_moisture, 1),
            "soil_temperature_c": round(base_soil_temp, 1),
            "soil_ph": round(base_ph, 2),
            "soil_ec_ds_m": round(base_ec, 2),
            "leaf_wetness_pct": round(base_leaf_wetness, 1),
            "air_temperature_c": round(air_temp, 1) if air_temp is not None else None,
            "air_humidity_pct": round(air_humidity, 1) if air_humidity is not None else None,
            "light_lux": round(float(base_light), 0),
            "battery_pct": round(battery, 1),
            "soil_nitrogen_ppm": round(base_n, 1),
            "soil_phosphorus_ppm": round(base_p, 1),
            "soil_potassium_ppm": round(base_k, 1),
            "rainfall_mm": round(base_rainfall, 1),
            "source": "simulated",
            "sensor_id": f"sim-field-{field.id}",
        }

    def force_simulate(self, field) -> "SensorReading":  # noqa: F821
        """Always generates+stores a NEW simulated reading, regardless of
        whether one already exists. Used by the manual 'Simulate Reading'
        demo action (mirrors the existing 'Refresh Weather' button) so a
        farmer without hardware yet can still see the panels update."""
        from backend.extensions import db
        from backend.models import SensorReading

        data = self.simulate_reading(field)
        reading = SensorReading(
            field_id=field.id,
            sensor_id=data.get("sensor_id"),
            source=data.get("source"),
            soil_moisture_pct=data.get("soil_moisture_pct"),
            soil_temperature_c=data.get("soil_temperature_c"),
            soil_ph=data.get("soil_ph"),
            soil_ec_ds_m=data.get("soil_ec_ds_m"),
            leaf_wetness_pct=data.get("leaf_wetness_pct"),
            air_temperature_c=data.get("air_temperature_c"),
            air_humidity_pct=data.get("air_humidity_pct"),
            light_lux=data.get("light_lux"),
            battery_pct=data.get("battery_pct"),
            soil_nitrogen_ppm=data.get("soil_nitrogen_ppm"),
            soil_phosphorus_ppm=data.get("soil_phosphorus_ppm"),
            soil_potassium_ppm=data.get("soil_potassium_ppm"),
            rainfall_mm=data.get("rainfall_mm"),
            raw_data=data,
        )
        db.session.add(reading)
        db.session.commit()
        return reading

    def fetch_or_simulate_latest(self, field) -> "SensorReading":  # noqa: F821
        """Returns the latest real SensorReading for `field` if one exists;
        otherwise generates+stores one simulated reading and returns that.
        Mirrors WeatherService's on-demand-fetch-if-empty pattern so a
        newly saved field never shows a permanently empty sensor panel."""
        from backend.extensions import db
        from backend.models import SensorReading

        latest = field.latest_sensor_reading()
        if latest is not None:
            return latest

        if not self.config.SIMULATE_IOT_DATA:
            return None  # type: ignore[return-value]

        data = self.simulate_reading(field)
        reading = SensorReading(
            field_id=field.id,
            sensor_id=data.get("sensor_id"),
            source=data.get("source"),
            soil_moisture_pct=data.get("soil_moisture_pct"),
            soil_temperature_c=data.get("soil_temperature_c"),
            soil_ph=data.get("soil_ph"),
            soil_ec_ds_m=data.get("soil_ec_ds_m"),
            leaf_wetness_pct=data.get("leaf_wetness_pct"),
            air_temperature_c=data.get("air_temperature_c"),
            air_humidity_pct=data.get("air_humidity_pct"),
            light_lux=data.get("light_lux"),
            battery_pct=data.get("battery_pct"),
            soil_nitrogen_ppm=data.get("soil_nitrogen_ppm"),
            soil_phosphorus_ppm=data.get("soil_phosphorus_ppm"),
            soil_potassium_ppm=data.get("soil_potassium_ppm"),
            rainfall_mm=data.get("rainfall_mm"),
            raw_data=data,
        )
        db.session.add(reading)
        db.session.commit()
        return reading


iot_service = IoTService()


def get_recent_readings(field, limit: int = 20):
    """Small helper used by feature_engineering.py: the field's N most
    recent SensorReading rows, newest first. Does NOT trigger simulation --
    callers that need "at least one reading" should call
    iot_service.fetch_or_simulate_latest(field) first."""
    return field.sensor_readings.limit(limit).all()