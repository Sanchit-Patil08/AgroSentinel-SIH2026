# AgroSentinel — AI-Powered Field Intelligence for Early Crop Disease & Pest Detection

**Smart India Hackathon 2026 — SIH26131**
Problem statement: *Early detection and management of crop diseases and pest infestations*
Organization: Government of Maharashtra · Category: Software · Theme: Agriculture, FoodTech & Rural Development

---

## 1. The idea

Most "AI crop disease detection" projects stop at *classify a photo of a leaf*. AgroSentinel is built around a bigger loop:

> Detect crop stress early → identify probable pest/disease risk → locate the affected zones in the field → support the farmer's intervention decision → record what was actually done → verify whether the field responded.

That last step — closing the loop *after* an intervention — is the part most systems skip, and it's the part this project is designed around from the database schema up (`Analysis` → `RiskAssessment` → `FieldDiagnosis` → `Intervention` → a new re-`Analysis` for before/after comparison).

## 2. End-to-end workflow

```
AGROSENTINEL
     │
     ▼
FIELD PROFILE
     │
 ┌───┼───┐
 ▼   ▼   ▼
Satellite  Weather  Sensors
 │   │   │
 └───┼───┘
     ▼
Feature / Stress Analysis
     ▼
Zone Mapping
     ▼
Risk Engine
     ▼
Diagnosis
     ▼
Intervention Engine
     ▼
Approved-use Pesticide Dataset
     ▼
Treatment Plans + Cost Estimate
     ▼
FARMER DECISION
     ▼
Intervention Record
     ▼
FOLLOW-UP WINDOW
     ▼
Re-analysis of same field
     ▼
Before vs After Verification
     ▼
OUTCOME
```

Instead of treating a farm as one unit, every field is split into zones so the system can say *which part* of the field is unusual, not just that "something looks off":

```
Whole Field → Analyze field → Find abnormal/stressed zones → Understand WHY
→ Assess pest/disease risk → Show evidence + confidence → Suggest interventions
→ Farmer chooses → Record intervention → Re-analyze later → Compare BEFORE vs AFTER
→ Determine field response
```

**Philosophy: don't just recommend — verify.**

## 3. Data sources

The pipeline is deliberately multi-source rather than dependent on one signal.

| Source | What it provides | Notes |
|---|---|---|
| 🛰️ **Satellite / remote sensing** | NDVI (vegetation health), NDRE (chlorophyll/nitrogen stress), SAVI (vegetation with reduced soil-background influence), NDMI (moisture stress) | Sentinel-2 style multispectral bands. Ships with a deterministic **sample-data generator** so the app runs with zero credentials; flip `USE_SAMPLE_DATA=0` and add Copernicus/Sentinel Hub credentials to go live — no other code changes needed. |
| 🌦️ **Weather** | Temperature, humidity, rainfall, wind, condition, historical/forecast context | Supporting *context*, not an independent diagnosis (e.g. high humidity + recent rain + crop stress → higher environmental risk). Same sample/live toggle pattern via `OPENWEATHER_API_KEY`, plus a background scheduler (`APScheduler`) that refreshes weather for every saved field on an interval. |
| 📡 **IoT sensors** | Soil moisture, soil temperature, pH, EC (salinity proxy), leaf wetness, air temp/humidity, light, battery, (planned) soil N/P/K + rain gauge | Real devices can `POST` readings at any time; when none exist yet, a deterministic simulated reading is generated (`SIMULATE_IOT_DATA=1`) so the pipeline always has something to compute with. |
| 🔬 **Hyperspectral verification** | Narrow-band spectral cross-check | Used selectively as a *deeper confirmation* layer for zones multispectral analysis already flagged moderate/stressed — not to cover the whole field. |

## 4. Pipeline architecture

```
FieldAnalyzer  (backend/services/field_analyzer.py)
  1. SatelliteService        → multispectral bands for the field bbox
  2. spectral_analysis        → NDVI / NDRE / SAVI / NDMI rasters
  3. zone_processor            → split field into zones, aggregate indices per zone
  4. HyperspectralService     → verify moderate/stressed zones (confidence boost)
  5. Field-health summary built for the map/UI

feature_engineering.py   → folds satellite + weather history + IoT history +
                             the field's own analysis history into ONE flat,
                             versioned "feature snapshot" dict (prefixed
                             sat_ / wx_ / iot_ / hist_ / ctx_) — the stable
                             contract a rule engine or a future ML model both
                             consume identically.

risk_engine.py           → transparent, weighted RULE ENGINE (not ML) that
                             turns a feature snapshot into risk level, score,
                             confidence, ranked causes, and recommendations.
                             Config.RISK_ENGINE_METHOD tags every stored row
                             so a future model ('ml_v1', ...) can be swapped
                             in behind the same function signature.

ml_risk_model.py         → separate, additive, INFERENCE-ONLY layer around a
                             lightweight offline-trained model
                             (ml/train_stress_model.py, HistGradientBoosting
                             on a public crop-stress dataset). Predicts a
                             stress probability only — it never invents
                             causes or recommendations; that stays the rule
                             engine's job. Degrades gracefully to
                             "unavailable" if no trained artifact exists yet.

diagnosis_engine.py      → Stage 2: once a farmer opts in, combines everything
                             Stage 1 already knows (zone stats, risk causes,
                             weather, sensors) with any ground evidence the
                             farmer adds (photos, notes) into a
                             confidence-aware hypothesis + what to verify
                             before treating anything.

intervention_engine.py   → Stage 3: rule-based (NOT a second diagnosis system)
                             mapping of RiskAssessment/FieldDiagnosis output to
                             MONITOR / VERIFY / TARGETED / FIELD_WIDE
                             recommendations, matched against the approved-use
                             pesticide dataset. Never claims a pesticide "will
                             cure" anything.

pesticide_data_service.py → the single query layer over the approved-use
                             pesticide dataset (PesticideUse table), shared by
                             the Intervention Engine and any future general
                             Pesticide Advisor tool — no duplicate database.
```

## 5. Data model (SQLAlchemy)

```
User (farmer account)
 └─ 1---* Field
      ├─ 1---* Analysis            (one row per "Analyze Field" run, never overwritten)
      │        └─ 1---* ZoneResult (per-zone NDVI/NDRE/SAVI/NDMI + hyperspectral verification)
      ├─ 1---* WeatherObservation  (history, on-demand + periodic scheduler)
      ├─ 1---* SensorReading       (IoT history — real, manual, or simulated)
      ├─ 1---* FeatureSnapshot     (versioned feature dict per analysis)
      ├─ 1---* RiskAssessment      (rule-engine output + optional ML prediction, side by side)
      └─ 1---* FieldDiagnosis
               ├─ 1---* DiagnosisEvidence  (farmer-submitted ground evidence)
               └─ 1---* Intervention        (what was actually done — closes the loop)

PesticideUse   (approved-use dataset, imported once, queried read-only)
```

Geometries (field boundary, zone polygons) are stored as portable GeoJSON in JSON columns rather than PostGIS geometry types, so the same schema runs unmodified on SQLite (dev) and PostgreSQL (production).

## 6. Tech stack

| Layer | Choice |
|---|---|
| Backend | Flask 3, Flask-SQLAlchemy, Flask-Login (session auth), Flask-CORS |
| Background jobs | APScheduler (in-process periodic weather refresh) |
| Geospatial | GeoPandas, Shapely, Rasterio, NumPy |
| Database | SQLite by default (`instance/agrosentinel.db`), PostgreSQL via `DATABASE_URL` for deployment — same ORM code either way |
| ML | scikit-learn `HistGradientBoostingRegressor` (offline-trained stress model, handles missing features natively) |
| Pest/disease image tool | Google Gemini (`gemini-2.5-flash`) — kept as an independent, standalone tool, not wired into the field-analysis pipeline |
| Frontend | Server-rendered Jinja templates + vanilla JS (no SPA framework) |

## 7. Project structure

```
AgroSentinel/
├── app.py                        # Flask entry point / app factory
├── requirements.txt
├── backend/
│   ├── config.py                 # all env-driven configuration & sample/live toggles
│   ├── extensions.py             # db, login_manager
│   ├── models.py                 # SQLAlchemy models (see §5)
│   ├── diagnosis_service.py
│   ├── ml_models/                # trained model artifact (.joblib, not committed)
│   ├── routes/                   # Flask blueprints: api, auth, fields, diagnosis,
│   │                              #   intervention, pest_disease
│   └── services/                 # the pipeline described in §4
├── ml/
│   ├── train_stress_model.py     # offline training script (run by hand, not imported by Flask)
│   ├── test_dataset.py
│   └── test_health_classifier.py
├── static/{css,js}/              # dashboard, field detail, diagnosis, intervention, etc.
├── templates/                    # login, register, dashboard, add_field, field_detail,
│                                  #   pest_disease, demo, index
└── instance/                     # SQLite DB + uploaded diagnosis photos (git-ignored)
```

## 8. Getting started

```bash
# 1. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment (see §9) — copy/create a .env file in the project root

# 4. Run
python app.py
# → http://localhost:5000
```

On first run, `db.create_all()` creates `instance/agrosentinel.db` automatically — no manual database setup needed. With every external toggle left at its default (`USE_SAMPLE_DATA=1`, no `OPENWEATHER_API_KEY`, `SIMULATE_IOT_DATA=1`), the app runs a full end-to-end demo with **zero external credentials**.

To train the optional ML stress-prediction layer:

```bash
pip install -r ml/requirements-train.txt
python ml/train_stress_model.py
```

This downloads a public Kaggle crop-stress dataset via `kagglehub`, maps only the columns that genuinely overlap with AgroSentinel's real feature schema, and writes `backend/ml_models/stress_model.joblib` + metadata. Until this has been run once, `ml_risk_model.py` reports predictions as gracefully "unavailable" rather than guessing.

To import the approved-use pesticide dataset (used by the Intervention Engine):

```bash
python backend/services/import_pesticide_data.py
```

## 9. Configuration

All configuration is environment-driven (`backend/config.py`, loaded via `python-dotenv`). Every external integration has a **sample-data fallback**, so nothing below is required to run or demo the project.

| Variable | Purpose | Default |
|---|---|---|
| `SECRET_KEY` | Flask session signing key | dev placeholder |
| `FLASK_DEBUG` | Debug mode | `1` |
| `USE_SAMPLE_DATA` | `1` = synthetic satellite data, `0` = live Sentinel Hub | `1` |
| `SH_CLIENT_ID` / `SH_CLIENT_SECRET` | Copernicus Data Space / Sentinel Hub OAuth client | — |
| `HYPERSPECTRAL_COLLECTION` | Hyperspectral collection id (e.g. PRISMA/EnMAP) | `enmap-l2a` |
| `OPENWEATHER_API_KEY` | Enables live weather (OpenWeatherMap); empty = sample weather | — |
| `WEATHER_UPDATE_INTERVAL_MINUTES` | Background weather refresh interval | `90` |
| `SIMULATE_IOT_DATA` | `1` = simulate sensor readings when none exist | `1` |
| `ML_STRESS_MODEL_ENABLED` | Toggle the ML prediction layer on/off | `1` |
| `GEMINI_API_KEY` | Required only for the standalone Pest & Disease image-detection tool | — |
| `DATABASE_URL` | PostgreSQL connection string for deployment; unset = local SQLite | — |
| `USE_POSTGIS` | Reserved for a future migration to true spatial columns | `0` |

> ⚠️ **Security note:** the archive this README was generated from contains a `.env` file with what look like real API keys/secrets (Sentinel Hub client secret, OpenWeatherMap key, Gemini key). `.env` is correctly listed in `.gitignore`, but **since these values may already be exposed, rotate/revoke them and confirm `.env` has never been committed to version control** before sharing or pushing this repository anywhere.

## 10. Key design principles carried through the codebase

- **Sample vs. live, everywhere.** Satellite, weather, and IoT services all share the same pattern: a deterministic sample generator by default, a drop-in live mode when credentials are supplied — no other code changes required either way.
- **Rules first, ML additive.** The risk engine is an explainable, weighted rule engine on purpose; the ML stress model sits *beside* it (same `RiskAssessment` row) rather than replacing it, and never fabricates a prediction when no trained artifact exists.
- **One dataset, one query layer.** The approved-use pesticide dataset is imported once and queried only through `pesticide_data_service.py`, so the Intervention Engine and any future advisor tool never duplicate or diverge.
- **Ownership-scoped everywhere.** Every field-scoped route funnels through a single `_get_owned_field_or_404` choke point so a farmer can only ever reach their own fields/analyses/diagnoses.
- **History, not overwrites.** Analyses, weather observations, sensor readings, and diagnoses all accumulate as new rows over time rather than overwriting the previous state — which is what makes the before/after verification loop possible.
- **Portable schema.** Geometries are stored as JSON/GeoJSON so the exact same code runs on SQLite in development and PostgreSQL in production.

## 11. Roadmap (noted in code, not yet built)

- Soil N/P/K + rain-gauge sensor columns already exist on `SensorReading` (nullable) ahead of that hardware.
- A general-purpose Pesticide Advisor tool (beyond the field-specific Intervention Engine) is stubbed as a disabled "Soon" nav item, sharing the same query layer.
- True PostGIS spatial columns (`ST_Intersects`, spatial indexes) behind the `USE_POSTGIS` flag, if/when scale requires it.
- Swapping the rule-based risk engine for a trained model behind the same `assess_risk(features) -> dict` signature.