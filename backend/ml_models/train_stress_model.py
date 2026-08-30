"""
ml/train_stress_model.py
-------------------------
Offline training script for AgroSentinel's lightweight ML crop-stress
model. Run this by hand (or in CI/a notebook) -- it is NOT imported by the
Flask app. The app only ever loads the artifact this script produces, via
backend/services/ml_risk_model.py.

WHAT THIS SCRIPT DOES, IN ORDER
1. Downloads the Kaggle dataset "Crop Health and Environmental Stress
   Dataset" (datasetengineer/crop-health-and-environmental-stress-dataset,
   ~212k rows) via `kagglehub`.
2. INSPECTS it programmatically -- shape, dtypes, describe(), missing-value
   counts, and each candidate feature's correlation with the label -- and
   prints a short report. If a column this script expects is missing (the
   dataset changed since this was written), it aborts with a clear error
   instead of silently training on the wrong thing.
3. Maps ONLY the columns that genuinely overlap with AgroSentinel's real
   FeatureSnapshot schema (backend/services/feature_engineering.py) onto
   their FeatureSnapshot names. No invented columns, no synthetic data.
   See FEATURE_MAP below for the exact mapping and any unit conversions.
4. Trains ONE lightweight, explainable model:
   sklearn.ensemble.HistGradientBoostingRegressor, predicting
   Crop_Stress_Indicator / 100 (a 0.0-1.0 "stress probability") from the
   8 overlapping features. This algorithm was picked specifically because
   it handles missing (NaN) feature values NATIVELY, at both train and
   predict time -- which matters because a real AgroSentinel field
   snapshot will often be missing some inputs (e.g. no IoT device
   deployed yet), and the brief requires graceful handling of that rather
   than fabricated values.
5. Evaluates on a held-out 20% split and prints/saves the REAL metrics
   (MAE, R^2). Nothing here fabricates an accuracy number -- if you retrain
   later on an updated dataset the numbers will differ, and that's the
   correct, honest behaviour.
6. Saves:
     backend/ml_models/stress_model.joblib           (the trained model)
     backend/ml_models/stress_model_metadata.json     (version, features
                                                        used, real metrics,
                                                        training timestamp)

WHY THE MAPPED FEATURE SET IS SMALL (8 features)
The Kaggle dataset does NOT include NDRE, NDMI, soil EC, soil temperature,
leaf wetness, or N/P/K -- so the model is honestly trained only on the
subset of AgroSentinel's real features that this dataset actually
provides: NDVI, SAVI, air temperature, humidity, rainfall, wind speed,
soil moisture, and soil pH. That is the genuinely compatible overlap
between the dataset and FeatureSnapshot -- see the "dataset compatibility"
note in ml/README.md for the full column-by-column reasoning.

HOW TO RUN
    pip install -r ml/requirements-train.txt
    # One-time Kaggle auth: https://www.kaggle.com/settings -> "Create New
    # Token" -> save as ~/.kaggle/kaggle.json (or export KAGGLE_USERNAME /
    # KAGGLE_KEY env vars). kagglehub reads either.
    python ml/train_stress_model.py

Safe to re-run any time -- it always overwrites the two output files.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = REPO_ROOT / "backend" / "ml_models"
MODEL_PATH = MODEL_DIR / "stress_model.joblib"
METADATA_PATH = MODEL_DIR / "stress_model_metadata.json"

DATASET_SLUG = "datasetengineer/crop-health-and-environmental-stress-dataset"

# FeatureSnapshot feature name -> (Kaggle column name, optional unit-conversion fn)
# Must stay in sync with the *names* produced by
# backend/services/feature_engineering.py (not the exact aggregation
# logic -- a single Kaggle row is one point-in-time reading, closest in
# spirit to feature_engineering's "latest"/"avg over a small window"
# fields when only one reading is available).
FEATURE_MAP = {
    "sat_mean_ndvi": ("NDVI", None),
    "sat_mean_savi": ("SAVI", None),
    "wx_avg_temperature_c": ("Temperature", None),
    "wx_avg_humidity_pct": ("Humidity", None),
    "wx_total_precip_mm": ("Rainfall", None),
    "wx_avg_wind_kmh": ("Wind_Speed", lambda s: s * 3.6),  # dataset is m/s -> project uses km/h
    "iot_avg_soil_moisture_pct": ("Soil_Moisture", None),
    "iot_avg_soil_ph": ("Soil_pH", None),
}
LABEL_COLUMN = "Crop_Stress_Indicator"  # 0 (no stress) - 100 (extreme stress) in the dataset

FEATURE_SCHEMA_VERSION = "v1"  # must track backend/config.py Config.FEATURE_SCHEMA_VERSION
MODEL_VERSION = "ml_v1"
RANDOM_STATE = 42
TEST_SIZE = 0.2


def download_dataset():
    """Downloads (or reuses the local kagglehub cache of) the dataset and
    returns it as a pandas DataFrame."""
    import pandas as pd

    try:
        import kagglehub
    except ImportError:
        sys.exit(
            "Missing dependency 'kagglehub'. Install training requirements first:\n"
            "    pip install -r ml/requirements-train.txt"
        )

    print(f"Downloading/locating dataset '{DATASET_SLUG}' via kagglehub ...")
    dataset_path = Path(kagglehub.dataset_download(DATASET_SLUG))

    csv_files = sorted(dataset_path.glob("*.csv"))
    if not csv_files:
        sys.exit(f"No CSV file found under {dataset_path} -- check the dataset contents on Kaggle.")
    # If more than one CSV ships in the dataset, the largest is the main table.
    csv_path = max(csv_files, key=lambda p: p.stat().st_size)
    print(f"Loading {csv_path.name} ...")
    return pd.read_csv(csv_path)


def inspect_and_validate(df) -> None:
    """Programmatic inspection step. Prints a compatibility report and
    ABORTS (does not train) if an expected column is missing."""
    print("\n=== Dataset inspection ===")
    print(f"rows={len(df):,}  columns={len(df.columns)}")

    expected_cols = [kcol for kcol, _ in FEATURE_MAP.values()] + [LABEL_COLUMN]
    missing_cols = [c for c in expected_cols if c not in df.columns]
    if missing_cols:
        sys.exit(
            "ABORTING -- expected column(s) not found in the dataset: "
            f"{missing_cols}\n"
            "The dataset schema may have changed since this script was written. "
            "Re-check the Kaggle dataset's column list and update FEATURE_MAP / "
            "LABEL_COLUMN in ml/train_stress_model.py before retraining."
        )

    print("\ncolumn stats (candidate features + label):")
    print(df[expected_cols].describe().T)

    print("\nmissing-value counts:")
    print(df[expected_cols].isna().sum())

    print(f"\ncorrelation of each candidate feature with {LABEL_COLUMN}:")
    corr = df[expected_cols].corr(numeric_only=True)[LABEL_COLUMN].drop(LABEL_COLUMN)
    print(corr.sort_values())
    print(
        "\n(These are the ACTUAL numbers from this run of the dataset -- "
        "nothing above is hard-coded.)"
    )


def build_training_frame(df):
    import pandas as pd

    out = pd.DataFrame(index=df.index)
    for feature_name, (kaggle_col, transform) in FEATURE_MAP.items():
        series = df[kaggle_col]
        out[feature_name] = transform(series) if transform else series

    label = (df[LABEL_COLUMN].astype(float) / 100.0).clip(0.0, 1.0)
    out["_label"] = label

    # Drop rows with a missing/invalid label -- can't train or evaluate on
    # those. Missing *feature* values are intentionally KEPT (as NaN) so
    # the model learns to handle them, since HistGradientBoostingRegressor
    # supports NaN inputs natively.
    before = len(out)
    out = out.dropna(subset=["_label"])
    dropped = before - len(out)
    if dropped:
        print(f"\nDropped {dropped} row(s) with a missing/invalid {LABEL_COLUMN}.")

    return out


def train_and_evaluate(frame):
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import mean_absolute_error, r2_score

    feature_names = list(FEATURE_MAP.keys())
    X = frame[feature_names]
    y = frame["_label"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=TEST_SIZE, random_state=RANDOM_STATE
    )

    model = HistGradientBoostingRegressor(
        max_iter=300,
        max_depth=6,
        learning_rate=0.06,
        random_state=RANDOM_STATE,
    )
    print(f"\nTraining HistGradientBoostingRegressor on {len(X_train):,} rows "
          f"({len(feature_names)} features) ...")
    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    mae = float(mean_absolute_error(y_test, preds))
    r2 = float(r2_score(y_test, preds))
    print(f"\nHoldout metrics (n_test={len(y_test):,}):  MAE={mae:.4f}   R^2={r2:.4f}")

    return model, {"mae": round(mae, 4), "r2": round(r2, 4), "n_test": int(len(y_test))}


def main():
    df = download_dataset()
    inspect_and_validate(df)
    frame = build_training_frame(df)
    model, holdout_metrics = train_and_evaluate(frame)

    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    import joblib
    joblib.dump(model, MODEL_PATH)

    metadata = {
        "model_version": MODEL_VERSION,
        "feature_version": FEATURE_SCHEMA_VERSION,
        "algorithm": "sklearn.ensemble.HistGradientBoostingRegressor",
        "features_used": list(FEATURE_MAP.keys()),
        "target": f"{LABEL_COLUMN} / 100  (0.0 = no stress, 1.0 = extreme stress)",
        "trained_on": {
            "dataset": DATASET_SLUG,
            "source": "kaggle",
            "rows_used": int(len(frame)),
        },
        "holdout_metrics": holdout_metrics,
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }
    METADATA_PATH.write_text(json.dumps(metadata, indent=2))

    print(f"\nSaved model    -> {MODEL_PATH}")
    print(f"Saved metadata -> {METADATA_PATH}")
    print("\nRestart the Flask app (or it will pick this up on next request) "
          "to start serving ML predictions on the field Overview tab.")


if __name__ == "__main__":
    main()