"""
ml_risk_model
-------------
Thin, INFERENCE-ONLY wrapper around the lightweight ML crop-stress model
trained offline by ml/train_stress_model.py (see that file's docstring for
the full how/why). This module is imported by the Flask app; it never
trains a model, never touches the network or Kaggle, and never touches the
database directly -- it only loads an artifact from disk and scores one
feature dict at a time.

THIS IS A PREDICTION LAYER, NOT A DECISION LAYER
predict() returns a stress probability (+ a risk-level bucket, for visual
consistency with the existing rule-based badge). It deliberately does NOT
produce causes or recommendations -- those stay entirely the job of
risk_engine.py's transparent rule engine, per the two-layer design:
    ML model        -> "how likely is this field under stress, and how
                         confident/complete was the input?"
    Rule engine      -> "why, and what should the farmer do about it?"
routes/fields.py calls both and stores them side by side on one
RiskAssessment row; neither replaces the other.

GRACEFUL DEGRADATION, ALWAYS
- No trained artifact on disk yet -> predict() returns
  {"available": False, ...} with an honest explanatory note. It NEVER
  invents a probability or a fake accuracy figure.
- A field missing some of the model's input features (e.g. no IoT sensor
  deployed yet) -> those inputs are passed to the model as NaN (never as
  a fabricated 0 or an average). The underlying algorithm
  (HistGradientBoostingRegressor) is chosen specifically because it
  handles NaN inputs natively at predict time. Which features were
  actually missing for this prediction is reported back in
  `features_missing` so the UI/farmer can see the prediction is partial.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

_MODEL_DIR = Path(__file__).resolve().parent.parent / "ml_models"
_MODEL_PATH = _MODEL_DIR / "stress_model.joblib"
_METADATA_PATH = _MODEL_DIR / "stress_model_metadata.json"

# Module-level cache: the artifact is loaded from disk at most once per
# process, not once per request.
_cache = {"loaded": False, "model": None, "metadata": None}


def _load():
    if _cache["loaded"]:
        return _cache["model"], _cache["metadata"]
    _cache["loaded"] = True

    if not _MODEL_PATH.exists() or not _METADATA_PATH.exists():
        logger.info(
            "ML stress model artifact not found at %s -- ML predictions "
            "will be reported as unavailable until `python ml/train_stress_model.py` "
            "is run.",
            _MODEL_PATH,
        )
        return None, None

    try:
        import joblib

        model = joblib.load(_MODEL_PATH)
        metadata = json.loads(_METADATA_PATH.read_text())
    except Exception:  # noqa: BLE001
        logger.exception("Failed to load ML stress model artifact -- treating as unavailable.")
        return None, None

    _cache["model"] = model
    _cache["metadata"] = metadata
    logger.info(
        "Loaded ML stress model %s (trained_at=%s)",
        metadata.get("model_version"),
        metadata.get("trained_at"),
    )
    return model, metadata


def is_available() -> bool:
    model, _ = _load()
    return model is not None


def _score_to_level(score: float) -> str:
    # Mirrors risk_engine._score_to_level's thresholds so the ML badge and
    # the rule-based badge read the same way at a glance.
    if score >= 0.75:
        return "critical"
    if score >= 0.5:
        return "high"
    if score >= 0.25:
        return "moderate"
    return "low"


def _unavailable(metadata=None, note=None, features_used=None, features_missing=None) -> Dict:
    return {
        "available": False,
        "stress_probability": None,
        "risk_level": None,
        "model_version": (metadata or {}).get("model_version"),
        "feature_version": (metadata or {}).get("feature_version"),
        "trained_on": ((metadata or {}).get("trained_on") or {}).get("dataset"),
        "holdout_metrics": (metadata or {}).get("holdout_metrics"),
        "features_used": features_used or [],
        "features_missing": features_missing or [],
        "note": note
        or "ML model not trained yet. Run `python ml/train_stress_model.py` to enable this prediction.",
    }


def predict(features: Dict) -> Dict:
    """features: the flat dict produced by
    backend.services.feature_engineering.build_feature_snapshot (the SAME
    dict already persisted as FeatureSnapshot.features -- no separate
    feature pipeline to keep in sync).

    Returns a plain, JSON-serializable dict -- safe to store directly on
    RiskAssessment.ml_prediction.
    """
    model, metadata = _load()
    if model is None:
        return _unavailable()

    features_used: List[str] = metadata.get("features_used", [])
    if not features_used:
        return _unavailable(metadata, note="Model metadata has no recorded feature list -- retrain to fix.")

    row = []
    missing: List[str] = []
    for name in features_used:
        val = features.get(name)
        if val is None:
            missing.append(name)
            row.append(float("nan"))
        else:
            try:
                row.append(float(val))
            except (TypeError, ValueError):
                missing.append(name)
                row.append(float("nan"))

    try:
        import numpy as np

        prediction = float(model.predict(np.array([row], dtype=float))[0])
    except Exception:  # noqa: BLE001
        logger.exception("ML stress model prediction failed at inference time.")
        return _unavailable(
            metadata,
            note="Prediction failed at inference time -- see server logs.",
            features_used=features_used,
            features_missing=missing,
        )

    prediction = max(0.0, min(prediction, 1.0))

    note = None
    if missing:
        note = (
            f"{len(missing)} of {len(features_used)} model input feature(s) "
            "were unavailable for this field (missing satellite/weather/IoT data) "
            "and were treated as missing, not fabricated."
        )

    return {
        "available": True,
        "stress_probability": round(prediction, 4),
        "risk_level": _score_to_level(prediction),
        "model_version": metadata.get("model_version"),
        "feature_version": metadata.get("feature_version"),
        "trained_on": (metadata.get("trained_on") or {}).get("dataset"),
        "holdout_metrics": metadata.get("holdout_metrics"),
        "features_used": features_used,
        "features_missing": missing,
        "note": note,
    }