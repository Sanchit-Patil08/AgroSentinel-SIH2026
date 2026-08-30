from typing import Dict, List, Tuple

import geopandas as gpd
import numpy as np
from shapely.geometry import Polygon, box, mapping

from backend.config import Config


def _grid_dimension(polygon: Polygon, area_ha: float) -> int:
    lo, hi = Config.TARGET_ZONE_COUNT_RANGE
    target = (lo + hi) / 2
    n = max(2, min(6, round(target ** 0.5)))

    if area_ha < 2:
        n = min(n, 3)

    return n


def compute_field_area_ha(polygon_coords: List[List[float]]) -> float:
    polygon = Polygon(polygon_coords)
    gdf = gpd.GeoDataFrame({"geometry": [polygon]}, crs="EPSG:4326")
    utm_crs = gdf.estimate_utm_crs()
    return float(gdf.to_crs(utm_crs).area.iloc[0] / 10000.0)


def compute_field_centroid(
    polygon_coords: List[List[float]],
) -> Tuple[float, float]:
    polygon = Polygon(polygon_coords)
    centroid = polygon.centroid
    return float(centroid.y), float(centroid.x)


def _calculate_adaptive_thresholds(ndvi_values: List[float]) -> Tuple[float, float]:
    values = np.array(
        [v for v in ndvi_values if v is not None and np.isfinite(v)],
        dtype=float,
    )

    if values.size < 3:
        return 0.6, 0.4

    q25 = float(np.percentile(values, 25))
    q60 = float(np.percentile(values, 60))

    healthy_threshold = max(0.55, q60)
    stressed_threshold = min(0.45, q25)

    if healthy_threshold - stressed_threshold < 0.08:
        midpoint = float(np.median(values))
        healthy_threshold = max(0.52, midpoint + 0.04)
        stressed_threshold = min(0.48, midpoint - 0.04)

    return healthy_threshold, stressed_threshold


def _classify_zone(
    ndvi: float,
    healthy_threshold: float,
    stressed_threshold: float,
) -> str:
    if ndvi >= healthy_threshold:
        return "healthy"

    if ndvi <= stressed_threshold:
        return "stressed"

    return "moderate"


def build_field_zones(
    polygon_coords: List[List[float]],
    bands: Dict,
    indices: Dict,
) -> Dict:
    polygon = Polygon(polygon_coords)

    gdf = gpd.GeoDataFrame(
        {"geometry": [polygon]},
        crs="EPSG:4326",
    )

    utm_crs = gdf.estimate_utm_crs()

    total_area_ha = float(
        gdf.to_crs(utm_crs).area.iloc[0] / 10000.0
    )

    minx, miny, maxx, maxy = polygon.bounds

    n = _grid_dimension(
        polygon,
        total_area_ha,
    )

    cell_w = (maxx - minx) / n
    cell_h = (maxy - miny) / n

    rows, cols = bands["resolution"]

    ndvi_arr = indices["ndvi"]
    ndre_arr = indices["ndre"]
    savi_arr = indices["savi"]
    ndmi_arr = indices["ndmi"]

    field_ndvi_values = ndvi_arr[np.isfinite(ndvi_arr)]

    if field_ndvi_values.size > 0:
        healthy_threshold, stressed_threshold = _calculate_adaptive_thresholds(
            field_ndvi_values.tolist()
        )
    else:
        healthy_threshold = 0.6
        stressed_threshold = 0.4

    zones = []
    zone_id = 1

    for i in range(n):
        cminy = miny + i * cell_h
        cmaxy = miny + (i + 1) * cell_h

        row_lo = max(
            0,
            int(
                (cminy - miny)
                / (maxy - miny)
                * rows
            ),
        )

        row_hi = min(
            rows,
            max(
                row_lo + 1,
                int(
                    (cmaxy - miny)
                    / (maxy - miny)
                    * rows
                ),
            ),
        )

        for j in range(n):
            cminx = minx + j * cell_w
            cmaxx = minx + (j + 1) * cell_w

            col_lo = max(
                0,
                int(
                    (cminx - minx)
                    / (maxx - minx)
                    * cols
                ),
            )

            col_hi = min(
                cols,
                max(
                    col_lo + 1,
                    int(
                        (cmaxx - minx)
                        / (maxx - minx)
                        * cols
                    ),
                ),
            )

            cell_poly = box(
                cminx,
                cminy,
                cmaxx,
                cmaxy,
            )

            clipped = cell_poly.intersection(polygon)

            if clipped.is_empty or clipped.area <= 0:
                continue

            clipped_gdf = gpd.GeoDataFrame(
                {"geometry": [clipped]},
                crs="EPSG:4326",
            )

            zone_area_ha = float(
                clipped_gdf.to_crs(utm_crs).area.iloc[0]
                / 10000.0
            )

            if zone_area_ha < Config.MIN_ZONE_AREA_HA:
                continue

            sub_ndvi = ndvi_arr[
                row_lo:row_hi,
                col_lo:col_hi,
            ]

            sub_ndre = ndre_arr[
                row_lo:row_hi,
                col_lo:col_hi,
            ]

            sub_savi = savi_arr[
                row_lo:row_hi,
                col_lo:col_hi,
            ]

            sub_ndmi = ndmi_arr[
                row_lo:row_hi,
                col_lo:col_hi,
            ]

            def safe_mean(
                array,
                fallback_array,
            ):
                valid = array[
                    np.isfinite(array)
                ]

                if valid.size:
                    return float(
                        np.mean(valid)
                    )

                fallback = fallback_array[
                    np.isfinite(fallback_array)
                ]

                if fallback.size:
                    return float(
                        np.mean(fallback)
                    )

                return 0.0

            mean_ndvi = safe_mean(
                sub_ndvi,
                ndvi_arr,
            )

            mean_ndre = safe_mean(
                sub_ndre,
                ndre_arr,
            )

            mean_savi = safe_mean(
                sub_savi,
                savi_arr,
            )

            mean_ndmi = safe_mean(
                sub_ndmi,
                ndmi_arr,
            )

            health_status = _classify_zone(
                mean_ndvi,
                healthy_threshold,
                stressed_threshold,
            )

            centroid = clipped.centroid

            zones.append(
                {
                    "zone_id": zone_id,
                    "geometry": mapping(clipped),
                    "centroid": (
                        centroid.x,
                        centroid.y,
                    ),
                    "area_ha": round(
                        zone_area_ha,
                        3,
                    ),
                    "ndvi": round(
                        mean_ndvi,
                        3,
                    ),
                    "ndre": round(
                        mean_ndre,
                        3,
                    ),
                    "savi": round(
                        mean_savi,
                        3,
                    ),
                    "ndmi": round(
                        mean_ndmi,
                        3,
                    ),
                    "health_status": health_status,
                }
            )

            zone_id += 1

    return {
        "zones": zones,
        "total_area_ha": round(
            total_area_ha,
            3,
        ),
        "bounds": (
            minx,
            miny,
            maxx,
            maxy,
        ),
        "classification_thresholds": {
            "healthy": round(
                healthy_threshold,
                3,
            ),
            "stressed": round(
                stressed_threshold,
                3,
            ),
        },
    }