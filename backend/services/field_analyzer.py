"""
FieldAnalyzer
--------------
Coordinates the full pipeline for one "Analyze Field" request:

  1. SatelliteService -> multispectral bands for the field bbox
  2. spectral_analysis -> NDVI / NDRE / SAVI / NDMI rasters
  3. zone_processor    -> split field into zones, aggregate indices/zone
  4. HyperspectralService -> verify moderate/stressed zones with a
     narrow-band spectral cross-check (deeper analysis / confirmation)
  5. Build the field-health summary shown next to the map
"""

from typing import Dict, List

from backend.services.satellite_service import SatelliteService
from backend.services.spectral_analysis import compute_all_indices
from backend.services.zone_processor import build_field_zones
from backend.services.hyperspectral_service import HyperspectralService

satellite_service = SatelliteService()
hyperspectral_service = HyperspectralService(satellite_service)


def analyze_field(polygon_coords: List[List[float]], crop_type: str, crop_stage: str) -> Dict:
    bbox = _bbox_of(polygon_coords)

    sat_data = satellite_service.get_multispectral_bands(polygon_coords, bbox)
    indices = compute_all_indices(sat_data["bands"])

    zoning = build_field_zones(polygon_coords, sat_data, indices)
    zones = zoning["zones"]

    # Hyperspectral verification: only run the deeper check on zones that
    # multispectral analysis flagged as moderate/stressed -- this mirrors
    # real operational use where hyperspectral is a targeted confirmation
    # layer, not a full-field sweep.
    for zone in zones:
        if zone["health_status"] in ("moderate", "stressed"):
            verification = hyperspectral_service.verify_zone(
                zone["centroid"], zoning["bounds"], zone["ndvi"]
            )
            zone["hyperspectral"] = verification
        else:
            zone["hyperspectral"] = None
        del zone["centroid"]  # not needed by the frontend

    summary = _build_summary(zones, zoning["total_area_ha"], crop_type, crop_stage, sat_data["observation_date"])

    return {
        "zones": zones,
        "summary": summary,
        "data_source": sat_data["source"],
    }


def _bbox_of(coords: List[List[float]]):
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    return (min(xs), min(ys), max(xs), max(ys))


def _build_summary(zones, total_area_ha, crop_type, crop_stage, observation_date) -> Dict:
    if not zones:
        return {
            "crop_type": crop_type,
            "crop_stage": crop_stage,
            "observation_date": observation_date,
            "analyzed_area_ha": total_area_ha,
            "overall_condition": "unknown",
            "mean_ndvi": None,
            "zone_stats": {"healthy": 0, "moderate": 0, "stressed": 0, "total": 0},
        }

    total_area = sum(z["area_ha"] for z in zones) or 1e-6
    weighted_ndvi = sum(z["ndvi"] * z["area_ha"] for z in zones) / total_area

    counts = {"healthy": 0, "moderate": 0, "stressed": 0}
    for z in zones:
        counts[z["health_status"]] += 1

    if weighted_ndvi >= 0.6:
        overall = "Healthy"
    elif weighted_ndvi >= 0.4:
        overall = "Moderate Stress"
    else:
        overall = "High Stress"

    return {
        "crop_type": crop_type,
        "crop_stage": crop_stage,
        "observation_date": observation_date,
        "analyzed_area_ha": round(total_area_ha, 2),
        "overall_condition": overall,
        "mean_ndvi": round(weighted_ndvi, 3),
        "zone_stats": {
            "healthy": counts["healthy"],
            "moderate": counts["moderate"],
            "stressed": counts["stressed"],
            "total": len(zones),
        },
    }