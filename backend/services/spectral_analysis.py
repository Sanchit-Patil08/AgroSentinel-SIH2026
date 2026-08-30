"""
spectral_analysis
------------------
Pure numpy computation of vegetation / spectral indices from multispectral
bands. Kept separate from data-fetching so the same functions work whether
bands came from sample data or a real Sentinel Hub response.
"""

import numpy as np


def _safe_ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    denom = np.where(np.abs(denominator) < 1e-6, 1e-6, denominator)
    return numerator / denom


def compute_ndvi(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    return _safe_ratio(nir - red, nir + red)


def compute_ndre(nir: np.ndarray, red_edge: np.ndarray) -> np.ndarray:
    return _safe_ratio(nir - red_edge, nir + red_edge)


def compute_savi(nir: np.ndarray, red: np.ndarray, L: float = 0.5) -> np.ndarray:
    return _safe_ratio(nir - red, nir + red + L) * (1 + L)


def compute_ndmi(nir: np.ndarray, swir: np.ndarray) -> np.ndarray:
    return _safe_ratio(nir - swir, nir + swir)


def compute_all_indices(bands: dict) -> dict:
    ndvi = compute_ndvi(bands["nir"], bands["red"])
    ndre = compute_ndre(bands["nir"], bands["red_edge"])
    savi = compute_savi(bands["nir"], bands["red"])
    ndmi = compute_ndmi(bands["nir"], bands["swir"])

    return {
        "ndvi": ndvi,
        "ndre": ndre,
        "savi": savi,
        "ndmi": ndmi
    }


def classify_health(
    ndvi_value: float,
    ndre_value: float = None,
    savi_value: float = None,
    ndmi_value: float = None,
    crop_stage: str = None
) -> str:
    """
    Multi-index, crop-stage-aware rule-based health classification.

    NDVI remains the primary indicator, while NDRE, SAVI and NDMI
    provide supporting evidence.

    Returns:
        healthy
        moderate
        stressed
    """

    if ndvi_value is None:
        return "stressed"

    score = 0.0

    stage = (crop_stage or "").lower()

    if "seed" in stage or "germination" in stage:
        healthy_ndvi = 0.45
        moderate_ndvi = 0.30
    elif "vegetative" in stage or "growth" in stage:
        healthy_ndvi = 0.55
        moderate_ndvi = 0.38
    elif "flower" in stage or "reproductive" in stage:
        healthy_ndvi = 0.60
        moderate_ndvi = 0.42
    elif "matur" in stage or "harvest" in stage:
        healthy_ndvi = 0.50
        moderate_ndvi = 0.35
    else:
        healthy_ndvi = 0.55
        moderate_ndvi = 0.38

    if ndvi_value >= healthy_ndvi:
        score += 2.0
    elif ndvi_value >= moderate_ndvi:
        score += 1.0

    if ndre_value is not None:
        if ndre_value >= 0.30:
            score += 1.5
        elif ndre_value >= 0.20:
            score += 0.75

    if savi_value is not None:
        if savi_value >= 0.45:
            score += 1.0
        elif savi_value >= 0.30:
            score += 0.5

    if ndmi_value is not None:
        if ndmi_value >= 0.30:
            score += 1.0
        elif ndmi_value >= 0.15:
            score += 0.5

    index_count = sum(
        value is not None
        for value in [ndvi_value, ndre_value, savi_value, ndmi_value]
    )

    if index_count == 0:
        return "stressed"

    max_possible = 5.5

    health_ratio = score / max_possible

    if health_ratio >= 0.62:
        return "healthy"

    if health_ratio >= 0.38:
        return "moderate"

    return "stressed"