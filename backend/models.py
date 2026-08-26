"""
Database models.

Schema overview
----------------
User (farmer account)
  1 --- * Field            (a farmer owns many fields)
Field
  1 --- * Analysis         (a field accumulates many analyses over time --
                             each "Analyze Field" click creates a NEW row,
                             never overwrites a previous one)
Analysis
  1 --- * ZoneResult       (each analysis is broken into many zones)

Design notes
------------
- Geometries (field boundary, zone polygons) are stored as GeoJSON in JSON
  columns rather than PostGIS geometry columns. This keeps the schema 100%
  portable between SQLite (dev) and PostgreSQL (production) with zero
  extension requirements. If/when real spatial SQL (ST_Intersects, spatial
  indexes, etc.) is needed at scale, these columns can be migrated to
  GeoAlchemy2 Geometry columns without touching the rest of the app, since
  all geometry access already goes through these model fields.
- Analysis carries a small `extra_data` JSON column and a `data_source`
  string specifically so future observation sources (weather API pulls,
  IoT sensor snapshots, additional hyperspectral passes) can be attached
  to an analysis -- or modeled as their own linked tables later -- without
  a schema rewrite. The pipeline is not hard-coded to "Sentinel-2 only".
- WeatherObservation is the first of those "future observation sources" --
  modeled as its own table (Field 1 --- * WeatherObservation) rather than
  bolted onto Analysis, since weather is fetched on its own schedule
  (periodic background job) independent of when a satellite analysis
  runs. This also gives a clean, ready-made shape for IoT sensor readings
  later: a small time-series table keyed on field_id + timestamp.
- crop_type / crop_stage are stored on Field (current values) AND snapshotted
  onto each Analysis (crop_type_snapshot / crop_stage_snapshot), so historical
  analyses still show what the crop info was *at the time of that analysis*
  even if the farmer later edits the field's crop details.
"""

from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from backend.extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)

    fields = db.relationship(
        "Field", back_populates="owner", cascade="all, delete-orphan", lazy="dynamic"
    )

    def set_password(self, raw_password: str) -> None:
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "email": self.email}


class Field(db.Model):
    __tablename__ = "fields"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    name = db.Column(db.String(150), nullable=False)
    # GeoJSON-style ring: [[lon, lat], [lon, lat], ...], first point == last point
    polygon = db.Column(db.JSON, nullable=False)

    crop_type = db.Column(db.String(80), nullable=False)
    crop_stage = db.Column(db.String(80), nullable=False)

    area_ha = db.Column(db.Float, nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    owner = db.relationship("User", back_populates="fields")
    analyses = db.relationship(
        "Analysis",
        back_populates="field",
        cascade="all, delete-orphan",
        order_by="desc(Analysis.created_at)",
        lazy="dynamic",
    )
    weather_observations = db.relationship(
        "WeatherObservation",
        back_populates="field",
        cascade="all, delete-orphan",
        order_by="desc(WeatherObservation.created_at)",
        lazy="dynamic",
    )

    def latest_analysis(self):
        return self.analyses.first()  # already ordered newest-first

    def latest_weather(self):
        return self.weather_observations.first()  # already ordered newest-first

    def to_dict(
        self,
        include_latest_analysis: bool = False,
        include_latest_weather: bool = False,
    ) -> dict:
        data = {
            "id": self.id,
            "name": self.name,
            "polygon": self.polygon,
            "crop_type": self.crop_type,
            "crop_stage": self.crop_stage,
            "area_ha": self.area_ha,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
        if include_latest_analysis:
            latest = self.latest_analysis()
            data["latest_analysis"] = latest.to_dict(include_zones=False) if latest else None
        if include_latest_weather:
            latest_w = self.latest_weather()
            data["latest_weather"] = latest_w.to_dict() if latest_w else None
        return data


class Analysis(db.Model):
    __tablename__ = "analyses"

    id = db.Column(db.Integer, primary_key=True)
    field_id = db.Column(db.Integer, db.ForeignKey("fields.id"), nullable=False, index=True)

    # Snapshot of crop info at the time this analysis ran
    crop_type_snapshot = db.Column(db.String(80), nullable=False)
    crop_stage_snapshot = db.Column(db.String(80), nullable=False)

    observation_date = db.Column(db.String(20), nullable=False)  # ISO date of satellite pass
    data_source = db.Column(db.String(40), nullable=False)  # 'sample' | 'sentinel-hub'

    analyzed_area_ha = db.Column(db.Float, nullable=True)
    overall_condition = db.Column(db.String(40), nullable=True)
    mean_ndvi = db.Column(db.Float, nullable=True)

    zone_healthy_count = db.Column(db.Integer, default=0, nullable=False)
    zone_moderate_count = db.Column(db.Integer, default=0, nullable=False)
    zone_stressed_count = db.Column(db.Integer, default=0, nullable=False)
    zone_total_count = db.Column(db.Integer, default=0, nullable=False)

    # Free-form slot for future data sources (weather snapshot, IoT
    # readings, model confidence, etc.) attached to this observation.
    extra_data = db.Column(db.JSON, nullable=True)

    created_at = db.Column(
        db.DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    field = db.relationship("Field", back_populates="analyses")
    zone_results = db.relationship(
        "ZoneResult", back_populates="analysis", cascade="all, delete-orphan", lazy="joined"
    )

    def to_dict(self, include_zones: bool = True) -> dict:
        data = {
            "id": self.id,
            "field_id": self.field_id,
            "crop_type": self.crop_type_snapshot,
            "crop_stage": self.crop_stage_snapshot,
            "observation_date": self.observation_date,
            "data_source": self.data_source,
            "analyzed_area_ha": self.analyzed_area_ha,
            "overall_condition": self.overall_condition,
            "mean_ndvi": self.mean_ndvi,
            "zone_stats": {
                "healthy": self.zone_healthy_count,
                "moderate": self.zone_moderate_count,
                "stressed": self.zone_stressed_count,
                "total": self.zone_total_count,
            },
            "created_at": self.created_at.isoformat(),
        }
        if include_zones:
            data["zones"] = [z.to_dict() for z in self.zone_results]
        return data


class ZoneResult(db.Model):
    __tablename__ = "zone_results"

    id = db.Column(db.Integer, primary_key=True)
    analysis_id = db.Column(
        db.Integer, db.ForeignKey("analyses.id"), nullable=False, index=True
    )

    zone_id = db.Column(db.Integer, nullable=False)  # per-analysis zone number
    geometry = db.Column(db.JSON, nullable=False)  # GeoJSON polygon

    area_ha = db.Column(db.Float, nullable=False)
    ndvi = db.Column(db.Float, nullable=False)
    ndre = db.Column(db.Float, nullable=True)
    savi = db.Column(db.Float, nullable=True)
    ndmi = db.Column(db.Float, nullable=True)
    health_status = db.Column(db.String(20), nullable=False, index=True)

    hyperspectral_confidence = db.Column(db.Float, nullable=True)
    hyperspectral_verified = db.Column(db.Boolean, nullable=True)

    analysis = db.relationship("Analysis", back_populates="zone_results")

    def to_dict(self) -> dict:
        hyperspectral = None
        if self.hyperspectral_confidence is not None:
            hyperspectral = {
                "confidence_pct": self.hyperspectral_confidence,
                "verified": self.hyperspectral_verified,
            }
        return {
            "zone_id": self.zone_id,
            "geometry": self.geometry,
            "area_ha": self.area_ha,
            "ndvi": self.ndvi,
            "ndre": self.ndre,
            "savi": self.savi,
            "ndmi": self.ndmi,
            "health_status": self.health_status,
            "hyperspectral": hyperspectral,
        }


class WeatherObservation(db.Model):
    """A single weather reading for a field, taken at its polygon centroid.

    Rows accumulate over time (one per fetch -- on-demand when a field is
    first saved/opened, and periodically from the background scheduler) so
    a weather *history* builds up per field, the same way Analysis rows
    accumulate a satellite-observation history.
    """

    __tablename__ = "weather_observations"

    id = db.Column(db.Integer, primary_key=True)
    field_id = db.Column(db.Integer, db.ForeignKey("fields.id"), nullable=False, index=True)

    # Point the reading was fetched for (the field's polygon centroid at
    # fetch time) -- kept for traceability/debugging, not shown prominently.
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)

    temperature_c = db.Column(db.Float, nullable=True)
    feels_like_c = db.Column(db.Float, nullable=True)
    humidity_pct = db.Column(db.Float, nullable=True)
    precipitation_mm = db.Column(db.Float, nullable=True)
    wind_speed_kmh = db.Column(db.Float, nullable=True)
    weather_condition = db.Column(db.String(80), nullable=True)  # e.g. 'Rain', 'Clear'
    weather_description = db.Column(db.String(160), nullable=True)

    source = db.Column(db.String(40), nullable=False)  # 'sample' | 'openweathermap'
    # Full raw provider payload, kept for future use (e.g. richer IoT-ready
    # fields later) without needing a schema migration.
    raw_data = db.Column(db.JSON, nullable=True)

    created_at = db.Column(
        db.DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    field = db.relationship("Field", back_populates="weather_observations")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "field_id": self.field_id,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "temperature_c": self.temperature_c,
            "feels_like_c": self.feels_like_c,
            "humidity_pct": self.humidity_pct,
            "precipitation_mm": self.precipitation_mm,
            "wind_speed_kmh": self.wind_speed_kmh,
            "weather_condition": self.weather_condition,
            "weather_description": self.weather_description,
            "source": self.source,
            "observed_at": self.created_at.isoformat(),
        }