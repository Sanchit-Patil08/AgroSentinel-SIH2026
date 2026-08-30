"""
Central configuration for AgroSentinel.

This file is the single place where the satellite-data architecture is
wired up. Right now the project ships with realistic SAMPLE DATA so the
demo runs with zero external credentials. To go live, fill in the
Copernicus Data Space / Sentinel Hub credentials below and flip
USE_SAMPLE_DATA to False -- no other code changes are required because
SatelliteService already branches on this flag.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# Project root (one level above /backend) -- used to place the default
# SQLite file at <project_root>/instance/agrosentinel.db
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
os.makedirs(INSTANCE_DIR, exist_ok=True)

# Field-diagnosis evidence photos (manual inspection uploads). Lives next
# to the SQLite file under instance/ -- git-ignored, same "not part of the
# repo" treatment as the DB itself. Served back out through an
# ownership-checked Flask route (backend/routes/diagnosis.py), never as a
# static/ file, so a farmer can only ever fetch their own evidence.
DIAGNOSIS_UPLOAD_DIR = os.path.join(INSTANCE_DIR, "uploads", "diagnosis")
os.makedirs(DIAGNOSIS_UPLOAD_DIR, exist_ok=True)


class Config:
    # --- General ---

    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    SECRET_KEY = os.getenv("SECRET_KEY", "agrosentinel-dev-key")
    DEBUG = os.getenv("FLASK_DEBUG", "1") == "1"

    # --- Satellite data source toggle ---
    # True  -> use built-in realistic sample generator (no internet/creds needed)
    # False -> call Copernicus Data Space / Sentinel Hub Process API
    USE_SAMPLE_DATA = os.getenv("USE_SAMPLE_DATA", "1") == "1"

    # --- Copernicus Data Space Ecosystem / Sentinel Hub credentials ---
    # Create an OAuth client at https://dataspace.copernicus.eu -> Sentinel Hub dashboard
    SH_CLIENT_ID = os.getenv("SH_CLIENT_ID", "")
    SH_CLIENT_SECRET = os.getenv("SH_CLIENT_SECRET", "")
    SH_TOKEN_URL = os.getenv(
        "SH_TOKEN_URL",
        "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
    )
    SH_PROCESS_URL = os.getenv(
        "SH_PROCESS_URL", "https://sh.dataspace.copernicus.eu/api/v1/process"
    )
    SH_CATALOG_URL = os.getenv(
        "SH_CATALOG_URL", "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search"
    )

    # --- Sentinel-2 (multispectral) collection used for field-level monitoring ---
    S2_COLLECTION = "sentinel-2-l2a"

    # --- Hyperspectral verification source ---
    # e.g. PRISMA / EnMAP / Sentinel Hub "hyperspectral" byoc collections.
    # Kept as a pluggable id so it can be swapped without touching logic.
    HYPERSPECTRAL_COLLECTION = os.getenv("HYPERSPECTRAL_COLLECTION", "enmap-l2a")

    # --- Weather data source (OpenWeatherMap Current Weather API) ---
    # Leave OPENWEATHER_API_KEY empty to use the built-in sample weather
    # generator -- same "sample vs live" pattern as the satellite pipeline
    # above, so the app runs/demoes with zero external credentials. Get a
    # free key at https://openweathermap.org/api and set it to go live --
    # no other code changes required, WeatherService already branches on it.
    OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
    OPENWEATHER_BASE_URL = os.getenv(
        "OPENWEATHER_BASE_URL", "https://api.openweathermap.org/data/2.5/weather"
    )

    # How often (minutes) the background scheduler refreshes weather for
    # every saved field. ~90 min keeps well within OpenWeatherMap's free
    # tier (60 calls/min) even with many fields, while still being
    # frequent enough for "continuous monitoring".
    WEATHER_UPDATE_INTERVAL_MINUTES = int(os.getenv("WEATHER_UPDATE_INTERVAL_MINUTES", "90"))

    # --- Zoning parameters ---
    MIN_ZONE_AREA_HA = 0.05     # zones smaller than this (after clipping) are dropped
    TARGET_ZONE_COUNT_RANGE = (6, 30)  # aim to split a field into this many zones

    # --- IoT sensor data (intelligence layer) ---
    # Same "sample vs live" pattern as weather: no real hardware is required
    # to demo/develop. When a field has no real device readings yet,
    # iot_service can generate a deterministic simulated reading on demand
    # (analogous to WeatherService._fetch_sample) so the sensor panel and
    # the feature/risk pipeline downstream always have something to work
    # with. Real devices push readings via POST /api/fields/<id>/sensors
    # at any time -- once real rows exist those are always preferred.
    SIMULATE_IOT_DATA = os.getenv("SIMULATE_IOT_DATA", "1") == "1"

    # --- Intelligence layer: feature engineering + risk engine ---
    # Number of most-recent WeatherObservation / SensorReading rows folded
    # into rolling-average / trend features for one FeatureSnapshot.
    FEATURE_WEATHER_WINDOW = int(os.getenv("FEATURE_WEATHER_WINDOW", "8"))
    FEATURE_SENSOR_WINDOW = int(os.getenv("FEATURE_SENSOR_WINDOW", "8"))
    FEATURE_SCHEMA_VERSION = "v1"

    # Current risk engine implementation tag, stored on every RiskAssessment
    # row so historical rows stay interpretable once a real ML model
    # ('ml_v1', ...) eventually replaces the rule-based scorer.
    RISK_ENGINE_METHOD = "rule_based_v1"

    # --- ML stress-prediction layer (backend/services/ml_risk_model.py) ---
    # This is a SEPARATE, additive prediction layer next to the rule-based
    # risk engine above -- see RiskAssessment.ml_prediction. The model
    # artifact itself (backend/ml_models/stress_model.joblib +
    # stress_model_metadata.json) is produced offline by
    # ml/train_stress_model.py and is NOT part of the git repo (see
    # .gitignore); until that script has been run once, ml_risk_model.py
    # gracefully reports predictions as unavailable rather than guessing.
    # Flip to "0" to skip even attempting to load/predict (e.g. to save a
    # few ms per analysis while iterating on something unrelated).
    ML_STRESS_MODEL_ENABLED = os.getenv("ML_STRESS_MODEL_ENABLED", "1") == "1"

    # --- Database (persistence layer) ---
    # Local/dev default: a file-based SQLite DB under /instance -- zero setup,
    # already a real server-style relational DB file, no external service
    # needed to run the hackathon demo.
    #
    # Production/deployment: set DATABASE_URL to a PostgreSQL connection
    # string (e.g. postgresql://user:password@host:5432/agrosentinel) and it
    # is used automatically -- no code changes required. SQLAlchemy's ORM
    # layer (db.Model, relationships, queries) is written against the ORM,
    # not raw SQL, so it runs unchanged on both SQLite and Postgres.
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", f"sqlite:///{os.path.join(INSTANCE_DIR, 'agrosentinel.db')}"
    )
    # Some managed Postgres providers (Heroku-style) hand out "postgres://"
    # URLs, which SQLAlchemy 1.4+/2.x rejects -- normalize transparently.
    if SQLALCHEMY_DATABASE_URI.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace(
            "postgres://", "postgresql://", 1
        )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- Field diagnosis (manual inspection evidence uploads) ---
    DIAGNOSIS_UPLOAD_DIR = DIAGNOSIS_UPLOAD_DIR
    DIAGNOSIS_ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "heic"}
    # Per-file cap (bytes). Applied in the route via Flask's
    # MAX_CONTENT_LENGTH so an oversized upload is rejected before it's
    # fully read into memory, not after.
    DIAGNOSIS_MAX_UPLOAD_MB = int(os.getenv("DIAGNOSIS_MAX_UPLOAD_MB", "12"))
    MAX_CONTENT_LENGTH = DIAGNOSIS_MAX_UPLOAD_MB * 1024 * 1024
    # How many of a field's most-stressed zones the diagnosis flow surfaces
    # as "priority zones to inspect" (mirrors the "Zone 4, 7, 12" example
    # in the brief rather than listing every stressed zone).
    DIAGNOSIS_MAX_PRIORITY_ZONES = 6

    # --- Optional PostGIS (not required -- geometries are stored as portable
    # GeoJSON/JSON columns so the schema works identically on SQLite and
    # Postgres; flip this on later if/when true spatial SQL queries such as
    # ST_Intersects are needed at scale). ---
    USE_POSTGIS = os.getenv("USE_POSTGIS", "0") == "1"