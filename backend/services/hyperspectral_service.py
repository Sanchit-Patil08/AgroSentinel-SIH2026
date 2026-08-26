"""
HyperspectralService
---------------------
Multispectral data (Sentinel-2 style) drives the main field-level
monitoring because it is frequent and freely available. Hyperspectral
data is comparatively scarce/coarse-revisit, so this service uses it the
way it is used operationally: as a deeper, narrow-band VERIFICATION layer
for zones that multispectral analysis already flagged as moderate/stressed
-- confirming the stress signature and boosting confidence, rather than
covering the whole field.
"""

from typing import Dict, Tuple

import numpy as np

from backend.services.satellite_service import SatelliteService


class HyperspectralService:
    def __init__(self, satellite_service: SatelliteService):
        self.satellite_service = satellite_service

    def verify_zone(
        self, centroid: Tuple[float, float], field_bbox: Tuple[float, float, float, float], ndvi_value: float
    ) -> Dict:
        """
        Pulls a simulated narrow-band reflectance signature at the zone
        centroid and derives a red-edge-position-based vigor estimate,
        then cross-checks it against the multispectral NDVI to produce a
        confidence score and a short verification note.
        """
        sig = self.satellite_service.get_hyperspectral_signature(centroid, field_bbox)
        wavelengths = np.array(sig["wavelengths_nm"])
        reflectance = np.array(sig["reflectance"])

        # Simple red-edge inflection proxy: reflectance jump between the
        # last red band (~680nm) and first NIR-ish band (~740-760nm)
        red_mask = wavelengths < 690
        nir_mask = wavelengths > 700
        red_mean = float(reflectance[red_mask].mean()) if red_mask.any() else 0.1
        nir_mean = float(reflectance[nir_mask].mean()) if nir_mask.any() else 0.3
        hyper_vigor = max(0.0, min(1.0, (nir_mean - red_mean) / (nir_mean + red_mean + 1e-6)))

        # Compare hyperspectral vigor estimate to multispectral NDVI (both ~0-1 scaled)
        agreement = 1.0 - min(1.0, abs(hyper_vigor - max(0.0, ndvi_value)))
        confidence = round(60 + agreement * 40, 1)  # 60-100% confidence band

        return {
            "hyperspectral_vigor_index": round(hyper_vigor, 3),
            "confidence_pct": confidence,
            "verified": agreement > 0.7,
            "bands_used": len(wavelengths),
        }