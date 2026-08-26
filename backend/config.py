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


class Config:
    # --- General ---
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

    # --- Optional PostGIS (not required -- geometries are stored as portable
    # GeoJSON/JSON columns so the schema works identically on SQLite and
    # Postgres; flip this on later if/when true spatial SQL queries such as
    # ST_Intersects are needed at scale). ---
    USE_POSTGIS = os.getenv("USE_POSTGIS", "0") == "1"