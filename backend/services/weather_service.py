"""
WeatherService
--------------
Responsible for obtaining CURRENT WEATHER data for a given lat/lon point.
Deliberately kept separate from the satellite pipeline (satellite_service.py
/ field_analyzer.py) -- weather is a different data source, fetched on a
different schedule, for a different purpose.

Two modes, controlled by whether Config.OPENWEATHER_API_KEY is set (the
same "sample vs live" pattern SatelliteService already uses):

  1. Sample mode (default, no credentials needed):
     Generates a deterministic, realistic synthetic weather reading so the
     app runs/demoes with zero external setup. Deterministic per
     location+hour, so re-fetching within the same hour gives a stable
     reading rather than random noise.

  2. Live mode:
     Calls the OpenWeatherMap "Current Weather Data" API
     (https://openweathermap.org/current), which only needs latitude +
     longitude -- exactly what a field's polygon centroid gives us, so the
     farmer never has to type in a city/location.

Both modes return the same dict shape so callers never need to know which
mode is active.

This module also owns the small orchestration step of turning a `Field`
into a persisted `WeatherObservation` row (`fetch_and_store_weather`),
since that exact step is needed from two places -- the "fetch once
immediately when a field is saved / first opened" path in routes/fields.py,
and the periodic background job in weather_scheduler.py -- and keeping it
here avoids duplicating that logic in both places.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

import requests

from backend.config import Config

logger = logging.getLogger(__name__)


class WeatherService:
    def __init__(self, config: Config = Config):
        self.config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_current_weather(self, lat: float, lon: float) -> Dict:
        """
        Returns a dict with:
          temperature_c, feels_like_c, humidity_pct, precipitation_mm,
          wind_speed_kmh, weather_condition, weather_description,
          source ('sample' | 'openweathermap'), raw (provider payload or None)
        """
        if self.config.OPENWEATHER_API_KEY:
            try:
                return self._fetch_live(lat, lon)
            except Exception:
                # Field monitoring should keep working even if the external
                # API is briefly down/rate-limited -- fall back to a clearly
                # labeled sample reading rather than losing the observation.
                logger.exception("OpenWeatherMap fetch failed, using sample fallback")
                return self._fetch_sample(lat, lon)
        return self._fetch_sample(lat, lon)

    # ------------------------------------------------------------------
    # Live mode
    # ------------------------------------------------------------------
    def _fetch_live(self, lat: float, lon: float) -> Dict:
        resp = requests.get(
            self.config.OPENWEATHER_BASE_URL,
            params={
                "lat": lat,
                "lon": lon,
                "appid": self.config.OPENWEATHER_API_KEY,
                "units": "metric",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        main = data.get("main", {})
        wind = data.get("wind", {})
        weather = (data.get("weather") or [{}])[0]
        rain = data.get("rain", {})
        snow = data.get("snow", {})
        precipitation = (rain.get("1h") or rain.get("3h") or 0) + (
            snow.get("1h") or snow.get("3h") or 0
        )

        return {
            "temperature_c": main.get("temp"),
            "feels_like_c": main.get("feels_like"),
            "humidity_pct": main.get("humidity"),
            "precipitation_mm": round(float(precipitation), 2),
            "wind_speed_kmh": round((wind.get("speed") or 0) * 3.6, 2),  # m/s -> km/h
            "weather_condition": weather.get("main"),
            "weather_description": (weather.get("description") or "").capitalize(),
            "source": "openweathermap",
            "raw": data,
        }

    # ------------------------------------------------------------------
    # Sample mode
    # ------------------------------------------------------------------
    def _fetch_sample(self, lat: float, lon: float) -> Dict:
        """Deterministic synthetic weather so the app works with zero
        credentials. Seeded on location + current hour so it's stable
        within an hour (like a real reading would be) rather than random
        on every call."""
        hour_bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H")
        seed_str = f"{round(lat, 3)}:{round(lon, 3)}:{hour_bucket}"
        seed = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)

        temp = 22 + (seed % 1500) / 100.0 - 7.5  # ~14.5-29.5 degrees C
        humidity = min(40 + (seed // 7 % 5000) / 100.0, 100.0)  # 40-90%
        wind = 3 + (seed // 13 % 2500) / 100.0  # 3-28 km/h

        rain_roll = (seed // 29) % 100
        precipitation = 0.0
        if rain_roll < 12:
            precipitation = round(4 + (seed // 31 % 1200) / 100.0, 2)
            condition, description = "Thunderstorm", "Heavy rainfall expected"
        elif rain_roll < 28:
            precipitation = round(1 + (seed // 31 % 400) / 100.0, 2)
            condition, description = "Rain", "Light to moderate rain expected"
        elif rain_roll < 50:
            condition, description = "Clouds", "Scattered clouds"
        else:
            condition, description = "Clear", "Clear sky"

        return {
            "temperature_c": round(temp, 1),
            "feels_like_c": round(temp - 1.2, 1),
            "humidity_pct": round(humidity, 1),
            "precipitation_mm": precipitation,
            "wind_speed_kmh": round(wind, 1),
            "weather_condition": condition,
            "weather_description": description,
            "source": "sample",
            "raw": None,
        }

    # ------------------------------------------------------------------
    # Simple, rule-based status summary (NOT ML / prediction -- just
    # thresholds on the current reading) used for the dashboard cards.
    # Richer risk scoring is intentionally deferred until satellite +
    # weather + IoT history has accumulated.
    # ------------------------------------------------------------------
    @staticmethod
    def summarize_status(observation: Optional[Dict]) -> Dict:
        if not observation:
            return {"level": "grey", "label": "No Data", "message": "Weather data not available yet"}

        condition = (observation.get("weather_condition") or "").lower()
        precipitation = observation.get("precipitation_mm") or 0
        wind = observation.get("wind_speed_kmh") or 0
        temp = observation.get("temperature_c")

        if precipitation >= 5 or "storm" in condition or "thunderstorm" in condition:
            return {"level": "yellow", "label": "Monitoring", "message": "Heavy rainfall expected"}
        if precipitation > 0 or "rain" in condition or "drizzle" in condition:
            return {"level": "yellow", "label": "Monitoring", "message": "Rain expected today"}
        if wind >= 35:
            return {"level": "yellow", "label": "Monitoring", "message": "Strong winds expected"}
        if temp is not None and (temp >= 42 or temp <= 4):
            return {"level": "yellow", "label": "Monitoring", "message": "Extreme temperature alert"}
        return {"level": "green", "label": "Monitoring Active", "message": "Weather looks stable today"}


weather_service = WeatherService()


def fetch_and_store_weather(field) -> "WeatherObservation":  # noqa: F821 - see import below
    """Computes `field`'s polygon centroid, fetches current weather for it,
    and persists a new WeatherObservation row. Shared by the on-demand path
    (field just saved / opened with no data yet) and the periodic
    background job -- see weather_scheduler.py."""
    # Imported here (not at module top) to avoid a circular import, since
    # models.py / extensions.py don't need to know about weather_service.
    from backend.extensions import db
    from backend.models import WeatherObservation
    from backend.services.zone_processor import compute_field_centroid

    lat, lon = compute_field_centroid(field.polygon)
    data = weather_service.get_current_weather(lat, lon)

    observation = WeatherObservation(
        field_id=field.id,
        latitude=lat,
        longitude=lon,
        temperature_c=data.get("temperature_c"),
        feels_like_c=data.get("feels_like_c"),
        humidity_pct=data.get("humidity_pct"),
        precipitation_mm=data.get("precipitation_mm"),
        wind_speed_kmh=data.get("wind_speed_kmh"),
        weather_condition=data.get("weather_condition"),
        weather_description=data.get("weather_description"),
        source=data.get("source"),
        raw_data=data.get("raw"),
    )
    db.session.add(observation)
    db.session.commit()
    return observation