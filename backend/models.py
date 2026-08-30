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
    sensor_readings = db.relationship(
        "SensorReading",
        back_populates="field",
        cascade="all, delete-orphan",
        order_by="desc(SensorReading.created_at)",
        lazy="dynamic",
    )
    feature_snapshots = db.relationship(
        "FeatureSnapshot",
        back_populates="field",
        cascade="all, delete-orphan",
        order_by="desc(FeatureSnapshot.created_at)",
        lazy="dynamic",
    )
    risk_assessments = db.relationship(
        "RiskAssessment",
        back_populates="field",
        cascade="all, delete-orphan",
        order_by="desc(RiskAssessment.created_at)",
        lazy="dynamic",
    )
    diagnoses = db.relationship(
        "FieldDiagnosis",
        back_populates="field",
        cascade="all, delete-orphan",
        order_by="desc(FieldDiagnosis.created_at)",
        lazy="dynamic",
    )

    def latest_analysis(self):
        return self.analyses.first()  # already ordered newest-first

    def latest_weather(self):
        return self.weather_observations.first()  # already ordered newest-first

    def latest_sensor_reading(self):
        return self.sensor_readings.first()  # already ordered newest-first

    def latest_risk_assessment(self):
        return self.risk_assessments.first()  # already ordered newest-first

    def latest_diagnosis(self):
        return self.diagnoses.first()  # already ordered newest-first

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


class SensorReading(db.Model):
    """A single IoT sensor reading for a field.

    Rows accumulate over time (one per reading) exactly like
    WeatherObservation, so a per-field sensor *history* builds up that the
    feature-engineering layer can pull rolling stats from.

    Not tied to a ZoneResult by foreign key on purpose: ZoneResult rows are
    regenerated on every "Analyze Field" run (the zone grid can shift), so
    they aren't a stable identity for a physical sensor to point at.
    Instead a sensor optionally carries its own lat/lon (for map placement
    and nearest-zone lookup at read time) and a free-text `zone_label` the
    farmer can set (e.g. "North corner", "Block 3") for human reference.

    Column set favors common low-cost agricultural IoT sensors (soil
    moisture/temp/pH/EC, leaf wetness, ambient temp/humidity, light).
    `raw_data` is a JSON escape hatch for any extra fields a specific
    device sends, so new sensor types don't require a migration.
    """

    __tablename__ = "sensor_readings"

    id = db.Column(db.Integer, primary_key=True)
    field_id = db.Column(db.Integer, db.ForeignKey("fields.id"), nullable=False, index=True)

    sensor_id = db.Column(db.String(80), nullable=True)  # device/station identifier
    zone_label = db.Column(db.String(80), nullable=True)  # farmer-facing sub-area name
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)

    soil_moisture_pct = db.Column(db.Float, nullable=True)
    soil_temperature_c = db.Column(db.Float, nullable=True)
    soil_ph = db.Column(db.Float, nullable=True)
    soil_ec_ds_m = db.Column(db.Float, nullable=True)  # electrical conductivity (salinity proxy)
    leaf_wetness_pct = db.Column(db.Float, nullable=True)
    air_temperature_c = db.Column(db.Float, nullable=True)
    air_humidity_pct = db.Column(db.Float, nullable=True)
    light_lux = db.Column(db.Float, nullable=True)
    battery_pct = db.Column(db.Float, nullable=True)

    # --- Planned sensor extension (soil nutrients + rain gauge) ---
    # Added to support the NPK/rainfall IoT hardware on the roadmap. All
    # nullable, same "graceful absence" pattern as every other sensor
    # column here -- a station that doesn't carry these probes yet simply
    # leaves them NULL, and downstream consumers (feature_engineering.py,
    # ml_risk_model.py) already treat NULL as "not available", never as 0.
    soil_nitrogen_ppm = db.Column(db.Float, nullable=True)   # N
    soil_phosphorus_ppm = db.Column(db.Float, nullable=True)  # P
    soil_potassium_ppm = db.Column(db.Float, nullable=True)   # K
    rainfall_mm = db.Column(db.Float, nullable=True)  # on-site tipping-bucket rain gauge (distinct from the Weather API's precipitation_mm)

    source = db.Column(db.String(40), nullable=False)  # 'manual' | 'device' | 'simulated'
    raw_data = db.Column(db.JSON, nullable=True)

    created_at = db.Column(
        db.DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    field = db.relationship("Field", back_populates="sensor_readings")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "field_id": self.field_id,
            "sensor_id": self.sensor_id,
            "zone_label": self.zone_label,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "soil_moisture_pct": self.soil_moisture_pct,
            "soil_temperature_c": self.soil_temperature_c,
            "soil_ph": self.soil_ph,
            "soil_ec_ds_m": self.soil_ec_ds_m,
            "leaf_wetness_pct": self.leaf_wetness_pct,
            "air_temperature_c": self.air_temperature_c,
            "air_humidity_pct": self.air_humidity_pct,
            "light_lux": self.light_lux,
            "battery_pct": self.battery_pct,
            "soil_nitrogen_ppm": self.soil_nitrogen_ppm,
            "soil_phosphorus_ppm": self.soil_phosphorus_ppm,
            "soil_potassium_ppm": self.soil_potassium_ppm,
            "rainfall_mm": self.rainfall_mm,
            "source": self.source,
            "observed_at": self.created_at.isoformat(),
        }


class FeatureSnapshot(db.Model):
    """A flat, versioned feature vector combining satellite + weather + IoT
    + historical-trend data at one point in time -- the reusable "ML-ready"
    structure the intelligence layer is built around.

    One snapshot is produced per Analysis run (see
    backend/services/feature_engineering.py). Keeping it as its own table
    (rather than stuffing it into Analysis.extra_data) means:
      - it has an explicit, versioned schema (`feature_version`) so a
        future ML model can be trained/served against a known contract,
      - historical snapshots can be queried/exported directly for model
        training without re-deriving them from raw Analysis/Weather/Sensor
        rows every time,
      - swapping the *engine* that consumes it (rule-based today, ML
        later) never requires touching how features are produced.

    `features` is intentionally a flat dict of name -> number/string so it
    maps directly onto a tabular ML feature matrix (one row per snapshot).
    """

    __tablename__ = "feature_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    field_id = db.Column(db.Integer, db.ForeignKey("fields.id"), nullable=False, index=True)
    analysis_id = db.Column(
        db.Integer, db.ForeignKey("analyses.id"), nullable=True, index=True, unique=True
    )

    feature_version = db.Column(db.String(20), nullable=False, default="v1")
    features = db.Column(db.JSON, nullable=False)

    created_at = db.Column(
        db.DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    field = db.relationship("Field", back_populates="feature_snapshots")
    analysis = db.relationship("Analysis", backref=db.backref("feature_snapshot", uselist=False))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "field_id": self.field_id,
            "analysis_id": self.analysis_id,
            "feature_version": self.feature_version,
            "features": self.features,
            "created_at": self.created_at.isoformat(),
        }


class RiskAssessment(db.Model):
    """Stress/risk analysis output for a field at a point in time.

    Produced by backend/services/risk_engine.py from a FeatureSnapshot.
    `method` records which engine produced the row ('rule_based_v1' today)
    so historical assessments remain interpretable even after a future ML
    model ('ml_v1', ...) starts writing these rows instead -- the schema
    does not change, only how the values are computed.

    Since the ML stress model was added, `risk_level`/`risk_score` (the
    field-level Healthy/Moderate/Stressed status) can come from EITHER
    engine on a given row -- `status_source` records which one actually
    won for that row (see routes/fields.py::_build_intelligence_layer).
    `causes`/`recommendations`/`confidence` are unaffected by that choice
    and always come from the rule engine, which is why `method` keeps
    describing them separately from `status_source`.
    """

    __tablename__ = "risk_assessments"

    id = db.Column(db.Integer, primary_key=True)
    field_id = db.Column(db.Integer, db.ForeignKey("fields.id"), nullable=False, index=True)
    analysis_id = db.Column(db.Integer, db.ForeignKey("analyses.id"), nullable=True, index=True)
    feature_snapshot_id = db.Column(
        db.Integer, db.ForeignKey("feature_snapshots.id"), nullable=True
    )

    risk_level = db.Column(db.String(20), nullable=False)  # 'low' | 'moderate' | 'high' | 'critical'
    risk_score = db.Column(db.Float, nullable=False)  # 0.0 - 1.0
    confidence = db.Column(db.Float, nullable=False)  # 0.0 - 1.0

    # Each: {"factor": str, "detail": str, "weight": float}
    causes = db.Column(db.JSON, nullable=False)
    # Each: {"title": str, "detail": str, "priority": "low"|"medium"|"high"}
    recommendations = db.Column(db.JSON, nullable=False)

    method = db.Column(db.String(40), nullable=False, default="rule_based_v1")

    # Which engine's output the *status* (risk_level + risk_score) actually
    # came from for this row: 'ml_v1' when the trained ML model was
    # available and produced a usable prediction, 'rule_based_v1' whenever
    # it fell back to the rule engine (model not trained, disabled, or
    # prediction failed). Independent of `method` above, which always
    # describes how causes/recommendations were produced (still always the
    # rule engine -- see risk_engine.py) -- see _build_intelligence_layer()
    # in routes/fields.py for exactly how the two are combined.
    status_source = db.Column(db.String(20), nullable=False, default="rule_based_v1")

    # Optional, additive: output of the lightweight ML stress model (see
    # backend/services/ml_risk_model.py), stored alongside -- never instead
    # of -- the rule-based risk_level/score/causes/recommendations above.
    # Shape: {"available": bool, "stress_probability": float|None,
    #         "risk_level": str|None, "model_version": str|None,
    #         "features_used": [str,...], "features_missing": [str,...],
    #         "trained_on": str|None, "note": str}
    # `available` is False (and the numeric fields are None) whenever no
    # trained model artifact exists yet -- this column NEVER carries a
    # fabricated prediction. recommendations always stay driven by
    # risk_engine.py's rule-based causes, per the two-layer design
    # (prediction layer vs. decision/recommendation layer). When
    # `available` is True, risk_level/risk_score above are sourced FROM
    # this prediction (see status_source) -- see routes/fields.py.
    ml_prediction = db.Column(db.JSON, nullable=True)

    created_at = db.Column(
        db.DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    field = db.relationship("Field", back_populates="risk_assessments")
    analysis = db.relationship("Analysis", backref=db.backref("risk_assessment", uselist=False))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "field_id": self.field_id,
            "analysis_id": self.analysis_id,
            "feature_snapshot_id": self.feature_snapshot_id,
            "risk_level": self.risk_level,
            "risk_score": round(self.risk_score, 3) if self.risk_score is not None else None,
            "confidence": round(self.confidence, 3) if self.confidence is not None else None,
            "causes": self.causes,
            "recommendations": self.recommendations,
            "method": self.method,
            "status_source": self.status_source,
            "ml_prediction": self.ml_prediction,
            "created_at": self.created_at.isoformat(),
        }

class FieldDiagnosis(db.Model):
    """One 'Diagnose This Field' inspection episode -- Stage 2 of the
    two-stage architecture described in the product brief:

        Stage 1 (existing): Sentinel-2 + Weather + IoT -> Analysis +
        RiskAssessment -> "something unusual may be happening".

        Stage 2 (this table): farmer-initiated, context-aware diagnosis
        that starts from the field's *existing* evidence (no
        re-entering crop/zone info) and lets the farmer add ground
        evidence -- today via manual photo upload, later via drone
        RGB/NoIR/thermal passes (see `inspection_method`).

    A diagnosis is NEVER a replacement for the satellite/weather/IoT
    pipeline and it never overwrites a previous diagnosis -- each
    "Diagnose This Field" click creates a new row (mirrors the
    Analysis-never-overwrites pattern above), so `Field.diagnoses`
    builds a chronological inspection history the farmer can look back
    over.

    `context_snapshot` freezes exactly what the farmer was shown when
    they started this diagnosis (crop/stage/area, zone stats, risk
    causes, weather, sensor reading) so the record stays meaningful
    even if the field is re-analyzed later. `priority_zones` is the
    small list of zone_ids the diagnosis flow asked the farmer to
    inspect, derived from the linked Analysis' stressed zones.

    IMPORTANT: this project does not ship a trained image-classification
    model. `possible_cause` / `confidence_level` are produced by a small,
    transparent rule engine (backend/services/diagnosis_engine.py) that
    combines the *already rule-based* RiskAssessment causes with
    farmer-labelled evidence (which image was uploaded, and an optional
    farmer-selected damage-pattern tag) -- never an invented AI
    classification of the photo pixels themselves. See that module's
    docstring for the reasoning.
    """

    __tablename__ = "field_diagnoses"

    id = db.Column(db.Integer, primary_key=True)
    field_id = db.Column(db.Integer, db.ForeignKey("fields.id"), nullable=False, index=True)
    analysis_id = db.Column(db.Integer, db.ForeignKey("analyses.id"), nullable=True)
    risk_assessment_id = db.Column(
        db.Integer, db.ForeignKey("risk_assessments.id"), nullable=True
    )

    # 'manual' today; 'drone' is accepted by the model/API shape now so the
    # future drone-evidence integration is a data addition, not a schema
    # rewrite -- but the drone flow itself is not implemented yet (see
    # backend/routes/diagnosis.py).
    inspection_method = db.Column(db.String(20), nullable=False, default="manual")

    # 'awaiting_evidence' -> farmer chose "Inspect Yourself", evidence not
    # uploaded/analyzed yet. 'diagnosed' -> run_diagnosis() has produced a
    # result (evidence may still be empty -- that just yields a
    # low-confidence, "insufficient evidence" result, per the brief).
    status = db.Column(db.String(20), nullable=False, default="awaiting_evidence")

    # Frozen view of field + zones + risk + weather + sensor at the moment
    # the farmer clicked "Diagnose This Field" -- see
    # diagnosis_engine.build_diagnosis_context().
    context_snapshot = db.Column(db.JSON, nullable=True)
    # [{zone_id, health_status, ndvi, area_ha}, ...] -- the zones the
    # farmer was asked to inspect for this diagnosis.
    priority_zones = db.Column(db.JSON, nullable=True)

    farmer_notes = db.Column(db.Text, nullable=True)

    # ---- diagnosis result (filled in by run_diagnosis / evidence-driven) ----
    possible_cause = db.Column(db.String(200), nullable=True)
    confidence_level = db.Column(db.String(20), nullable=True)  # 'low' | 'medium' | 'high'
    confidence_score = db.Column(db.Float, nullable=True)  # 0.0 - 1.0
    supporting_evidence = db.Column(db.JSON, nullable=True)  # [str, ...]
    recommended_verification = db.Column(db.JSON, nullable=True)  # [str, ...]
    recommended_intervention = db.Column(db.JSON, nullable=True)  # [str, ...]
    protection_alert = db.Column(db.Text, nullable=True)  # beneficial-insect caution, if any

    pest_detection = db.Column(db.JSON, nullable=True)
    disease_detection = db.Column(db.JSON, nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False, index=True)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    field = db.relationship("Field", back_populates="diagnoses")
    evidence_items = db.relationship(
        "DiagnosisEvidence",
        back_populates="diagnosis",
        cascade="all, delete-orphan",
        order_by="DiagnosisEvidence.created_at",
        lazy="joined",
    )

    def to_dict(self, include_evidence: bool = True) -> dict:
        data = {
            "id": self.id,
            "field_id": self.field_id,
            "analysis_id": self.analysis_id,
            "risk_assessment_id": self.risk_assessment_id,
            "inspection_method": self.inspection_method,
            "status": self.status,
            "context_snapshot": self.context_snapshot,
            "priority_zones": self.priority_zones,
            "farmer_notes": self.farmer_notes,
            "possible_cause": self.possible_cause,
            "confidence_level": self.confidence_level,
            "confidence_score": (
                round(self.confidence_score, 3) if self.confidence_score is not None else None
            ),
            "supporting_evidence": self.supporting_evidence,
            "recommended_verification": self.recommended_verification,
            "recommended_intervention": self.recommended_intervention,
            "protection_alert": self.protection_alert,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
        if include_evidence:
            data["evidence"] = [e.to_dict() for e in self.evidence_items]
        else:
            data["evidence_count"] = len(self.evidence_items)
        return data


class DiagnosisEvidence(db.Model):
    """One farmer-uploaded evidence image attached to a FieldDiagnosis.

    Files are stored on disk under instance/uploads/diagnosis/<diagnosis_id>/
    (instance/ already holds the SQLite DB and is git-ignored) -- only the
    relative path is kept in the DB, mirroring how geometries are kept
    portable elsewhere in this schema. `image_type` is the farmer's own
    label for what the photo shows; `damage_pattern` is an optional
    farmer-selected tag (chewing / sucking / curling / wilting / leaf_spot
    / discoloration / not_sure) used by the rule-based diagnosis engine.
    Neither field is an AI-generated classification -- see FieldDiagnosis
    docstring.
    """

    __tablename__ = "diagnosis_evidence"

    id = db.Column(db.Integer, primary_key=True)
    diagnosis_id = db.Column(
        db.Integer, db.ForeignKey("field_diagnoses.id"), nullable=False, index=True
    )
    analysis_id = db.Column(
        db.Integer, db.ForeignKey("analyses.id"), nullable=True, index=True
    )

    zone_id = db.Column(db.Integer, nullable=True, index=True)

    # 'leaf' | 'pest_insect' | 'closeup' | 'beneficial_insect' | 'other'
    image_type = db.Column(db.String(30), nullable=False, default="other")
    damage_pattern = db.Column(db.String(30), nullable=True)
    note = db.Column(db.Text, nullable=True)

    file_path = db.Column(db.String(300), nullable=False)  # relative to instance/uploads
    original_filename = db.Column(db.String(200), nullable=True)
    content_type = db.Column(db.String(80), nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)

    diagnosis = db.relationship("FieldDiagnosis", back_populates="evidence_items")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "diagnosis_id": self.diagnosis_id,
            "analysis_id": self.analysis_id,
            "image_type": self.image_type,
            "damage_pattern": self.damage_pattern,
            "note": self.note,
            "original_filename": self.original_filename,
            "url": f"/api/fields/{self.diagnosis.field_id}/diagnosis/{self.diagnosis_id}/evidence/{self.id}/file",
            "created_at": self.created_at.isoformat(),
        }

class PesticideUse(db.Model):
    """One government-approved-use record: a single (insecticide, crop,
    pest) combination with its reference dosage/formulation/spray-fluid
    range, sourced from the "Approved uses of registered insecticides"
    dataset (see scripts/import_pesticide_data.py).

    This is the ONE authoritative pesticide-use table in the project --
    both the field-specific Intervention Engine
    (backend/services/intervention_engine.py) and the future general
    Pesticide Advisor tool query it through the same reusable functions
    in backend/services/pesticide_data_service.py. Nothing here is
    LLM-generated or hallucinated; every row traces back to a row printed
    in the source PDF (`source_page`) and the importer never invents a
    dosage.

    Normalization notes (see the importer for the actual synonym maps):
      - `crop` / `pest` keep the ORIGINAL text exactly as printed in the
        source (never mutated), so the source record is always
        recoverable and auditable.
      - `crop_normalized` / `pest_normalized` hold a lowercased, synonym-
        collapsed form (e.g. "paddy" and "rice" both normalize to
        "rice"; "bph" and "brown plant hopper" both normalize to "brown
        plant hopper") purely so lookups like
        search_pesticides(crop="Rice", pest="Brown Plant Hopper") can
        find a record whose source row says "Paddy" / "BPH" -- the
        normalization is additive metadata for search, never a
        destructive rewrite of the source fields, and different
        pesticides/dosages are never merged into one row.
      - One source table row that lists several pests together in one
        cell (e.g. "Stem Borer, Leaf Folder, Plant Hoppers") is split
        into one PesticideUse row PER pest, all sharing the same
        dosage/formulation/spray-fluid figures and the same
        `source_row_group` id -- so each pest is independently
        searchable while still traceable back to the single printed row
        it came from.
    """

    __tablename__ = "pesticide_uses"

    id = db.Column(db.Integer, primary_key=True)

    insecticide = db.Column(db.String(150), nullable=False, index=True)

    crop = db.Column(db.String(120), nullable=False, index=True)
    crop_normalized = db.Column(db.String(120), nullable=False, index=True)

    pest = db.Column(db.String(200), nullable=False, index=True)
    pest_normalized = db.Column(db.String(200), nullable=False, index=True)

    # Kept as free-text strings, never parsed into floats: the source
    # dataset mixes plain numbers ("500"), ranges ("500-750"), "X to Y"
    # ranges, per-plant/per-tree units, and "_" for "not specified" --
    # collapsing that into a single numeric column would silently invent
    # precision the source doesn't have. The Intervention Simulator
    # (backend/services/intervention_engine.py) parses a usable
    # min/max out of these at calculation time instead.
    dosage_ai_gm_ha = db.Column(db.String(60), nullable=True)
    formulation_dosage = db.Column(db.String(60), nullable=True)
    spray_fluid = db.Column(db.String(60), nullable=True)

    # Which source PDF this row came from + the page it was printed on,
    # for traceability/debugging (e.g. "Pesticide_list_pesticide_wise.pdf p.3").
    source = db.Column(db.String(200), nullable=False)
    # Rows split out of the same original table row (see class docstring)
    # share this id so a UI/debug view can reassemble "what one printed
    # row actually said".
    source_row_group = db.Column(db.String(40), nullable=True, index=True)

    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint(
            "insecticide", "crop", "pest", "dosage_ai_gm_ha",
            name="uq_pesticide_use_record",
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "insecticide": self.insecticide,
            "crop": self.crop,
            "pest": self.pest,
            "dosage_ai_gm_ha": self.dosage_ai_gm_ha,
            "formulation_dosage": self.formulation_dosage,
            "spray_fluid": self.spray_fluid,
            "source": self.source,
        }
