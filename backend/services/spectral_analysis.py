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
    """Normalized Difference Vegetation Index: general vegetation vigor."""
    return _safe_ratio(nir - red, nir + red)


def compute_ndre(nir: np.ndarray, red_edge: np.ndarray) -> np.ndarray:
    """Normalized Difference Red Edge: sensitive to early/subtle stress
    before it is visible in NDVI, useful for early detection."""
    return _safe_ratio(nir - red_edge, nir + red_edge)


def compute_savi(nir: np.ndarray, red: np.ndarray, L: float = 0.5) -> np.ndarray:
    """Soil Adjusted Vegetation Index: reduces soil-background noise,
    useful early in the season / sparse canopy."""
    return _safe_ratio((nir - red), (nir + red + L)) * (1 + L)


def compute_ndmi(nir: np.ndarray, swir: np.ndarray) -> np.ndarray:
    """Normalized Difference Moisture Index: canopy water content proxy."""
    return _safe_ratio(nir - swir, nir + swir)


def compute_all_indices(bands: dict) -> dict:
    ndvi = compute_ndvi(bands["nir"], bands["red"])
    ndre = compute_ndre(bands["nir"], bands["red_edge"])
    savi = compute_savi(bands["nir"], bands["red"])
    ndmi = compute_ndmi(bands["nir"], bands["swir"])
    return {"ndvi": ndvi, "ndre": ndre, "savi": savi, "ndmi": ndmi}


def classify_health(ndvi_value: float) -> str:
    """Maps a mean NDVI value to a health category used for zone coloring."""
    if ndvi_value >= 0.6:
        return "healthy"
    if ndvi_value >= 0.4:
        return "moderate"
    return "stressed"