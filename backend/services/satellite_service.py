"""
SatelliteService
-----------------
Responsible for obtaining MULTISPECTRAL band data (Sentinel-2 style: Red,
NIR, RedEdge, SWIR) for a given field geometry and time window.

Two modes, controlled by Config.USE_SAMPLE_DATA:

  1. Sample mode (default, no credentials needed):
     Generates a deterministic, spatially-coherent synthetic reflectance
     field so demos look realistic and are reproducible for the same
     polygon + date.

  2. Live mode:
     Talks to the Copernicus Data Space Ecosystem's Sentinel Hub Process
     API. The request-building code is included and ready to use -- only
     valid SH_CLIENT_ID / SH_CLIENT_SECRET are required to switch over.

Both modes return the exact same data shape so the rest of the pipeline
(spectral_analysis, zone_processor) never needs to know which mode is
active.
"""

from __future__ import annotations

import hashlib
import math
import io
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

import numpy as np
import requests
import rasterio

from backend.config import Config


class SatelliteService:
    def __init__(self, config: Config = Config):
        self.config = config
        self._token_cache: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_multispectral_bands(
        self, polygon_coords: List[List[float]], bbox: Tuple[float, float, float, float]
    ) -> Dict:
        """
        Returns a dict with:
          - bands: {'red': ndarray, 'nir': ndarray, 'red_edge': ndarray, 'swir': ndarray}
          - grid_bbox: (minx, miny, maxx, maxy) the bands correspond to
          - resolution: (rows, cols)
          - observation_date: ISO date string of the (simulated or real) satellite pass
          - source: 'sample' | 'sentinel-hub'
        """
        if self.config.USE_SAMPLE_DATA or not (
            self.config.SH_CLIENT_ID and self.config.SH_CLIENT_SECRET
        ):
            return self._generate_sample_bands(polygon_coords, bbox)
        return self._fetch_sentinel_hub_bands(polygon_coords, bbox)

    def get_hyperspectral_signature(
        self, point: Tuple[float, float], seed_bbox: Tuple[float, float, float, float]
    ) -> Dict:
        """
        Returns a simplified simulated hyperspectral reflectance signature
        (a handful of narrow bands across VNIR) at a given point, used by
        HyperspectralService to cross-verify multispectral stress zones.
        Architecture-ready for a real EnMAP/PRISMA byoc collection call.
        """
        if self.config.USE_SAMPLE_DATA or not (
            self.config.SH_CLIENT_ID and self.config.SH_CLIENT_SECRET
        ):
            return self._generate_sample_hyperspectral(point, seed_bbox)
        return self._fetch_hyperspectral_live(point)

    # ------------------------------------------------------------------
    # Sample data generation (deterministic, seeded by geometry)
    # ------------------------------------------------------------------
    def _seed_from_bbox(self, bbox: Tuple[float, float, float, float]) -> int:
        key = ",".join(f"{v:.6f}" for v in bbox)
        return int(hashlib.md5(key.encode()).hexdigest()[:8], 16)

    def _generate_sample_bands(self, polygon_coords, bbox) -> Dict:
        minx, miny, maxx, maxy = bbox
        seed = self._seed_from_bbox(bbox)
        rng = np.random.default_rng(seed)

        # Grid resolution: keep it light for a fast demo (~ Sentinel-2 10m
        # conceptually, but downsampled for responsiveness)
        rows, cols = 60, 60

        # Build smooth, spatially-correlated "stress" surface using layered
        # sine noise + a few random low-vigor patches, so it looks like a
        # real field rather than pure random noise.
        y = np.linspace(0, 1, rows).reshape(-1, 1)
        x = np.linspace(0, 1, cols).reshape(1, -1)

        base = 0.75 + 0.05 * np.sin(2 * math.pi * (x * 2 + y * 1.3) + seed % 10)
        base += 0.03 * np.cos(2 * math.pi * (x * 4 - y * 2))

        # Add 2-4 stress patches (lower vigor blobs)
        n_patches = 2 + seed % 3
        for i in range(n_patches):
            cx, cy = rng.uniform(0.1, 0.9), rng.uniform(0.1, 0.9)
            radius = rng.uniform(0.08, 0.22)
            intensity = rng.uniform(0.15, 0.35)
            dist = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            base -= intensity * np.exp(-(dist ** 2) / (2 * radius ** 2))

        vigor = np.clip(base, 0.05, 0.95)  # 0=stressed .. 1=vigorous, drives NDVI

        # Derive plausible surface reflectance bands (0..1) from vigor.
        # Healthy vegetation: low red, high NIR -> high NDVI.
        red = np.clip(0.12 + (1 - vigor) * 0.18 + rng.normal(0, 0.01, (rows, cols)), 0.02, 0.6)
        nir = np.clip(0.28 + vigor * 0.45 + rng.normal(0, 0.015, (rows, cols)), 0.05, 0.9)
        red_edge = np.clip(0.15 + vigor * 0.30 + rng.normal(0, 0.01, (rows, cols)), 0.05, 0.8)
        swir = np.clip(0.15 + (1 - vigor) * 0.20 + rng.normal(0, 0.01, (rows, cols)), 0.05, 0.6)

        # Simulated most-recent cloud-free Sentinel-2 pass (within last 5 days)
        days_ago = seed % 5
        obs_date = (datetime.utcnow() - timedelta(days=days_ago)).date().isoformat()

        return {
            "bands": {"red": red, "nir": nir, "red_edge": red_edge, "swir": swir},
            "vigor": vigor,
            "grid_bbox": (minx, miny, maxx, maxy),
            "resolution": (rows, cols),
            "observation_date": obs_date,
            "source": "sample",
        }

    def _generate_sample_hyperspectral(self, point, seed_bbox) -> Dict:
        seed = self._seed_from_bbox(seed_bbox) + int(abs(point[0] * 1000) + abs(point[1] * 1000))
        rng = np.random.default_rng(seed)
        # Simplified 10-band VNIR signature (450-950nm) as reflectance %
        wavelengths = np.linspace(450, 950, 10)
        vigor = rng.uniform(0.2, 0.95)
        # Vegetation red-edge shape: rises sharply ~700-750nm when healthy
        signature = 0.05 + 0.02 * (wavelengths < 680) + vigor * 0.5 * (wavelengths > 700)
        signature += rng.normal(0, 0.01, size=10)
        signature = np.clip(signature, 0.01, 0.9)
        return {
            "wavelengths_nm": wavelengths.tolist(),
            "reflectance": signature.tolist(),
            "vigor_estimate": float(vigor),
            "source": "sample",
        }

    # ------------------------------------------------------------------
    # Live Copernicus Data Space / Sentinel Hub integration
    # ------------------------------------------------------------------
    def _get_oauth_token(self) -> str:
        if "token" in self._token_cache:
            return self._token_cache["token"]
        resp = requests.post(
            self.config.SH_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.config.SH_CLIENT_ID,
                "client_secret": self.config.SH_CLIENT_SECRET,
            },
            timeout=20,
        )
        resp.raise_for_status()
        token = resp.json()["access_token"]
        self._token_cache["token"] = token
        return token

    def _fetch_sentinel_hub_bands(self, polygon_coords, bbox) -> Dict:
        token = self._get_oauth_token()

        today = datetime.now(timezone.utc).date()
        from_date = today - timedelta(days=30)

        evalscript = """
        //VERSION=3

        function setup() {

            return {
                input: [
                    {
                        bands: [
                            "B04",
                            "B05",
                            "B08",
                            "B11",
                            "dataMask"
                        ],
                        units: "REFLECTANCE"
                    }
                ],

                output: {
                    bands: 5,
                    sampleType: "FLOAT32"
                }
            };
        }

        function evaluatePixel(sample) {

            return [
                sample.B04,
                sample.B05,
                sample.B08,
                sample.B11,
                sample.dataMask
            ];
        }
        """

        payload = {
            "input": {
                "bounds": {
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [polygon_coords]
                    },
                    "properties": {
                        "crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
                    }
                },
                "data": [
                    {
                        "type": "sentinel-2-l2a",
                        "dataFilter": {
                            "timeRange": {
                                "from": f"{from_date}T00:00:00Z",
                                "to": f"{today}T23:59:59Z"
                            },
                            "mosaickingOrder": "mostRecent"
                        }
                    }
                ]
            },
            "output": {
                "width": 256,
                "height": 256,
                "responses": [
                    {
                        "identifier": "default",
                        "format": {
                            "type": "image/tiff"
                        }
                    }
                ]
            },
            "evalscript": evalscript
        }

        response = requests.post(
            self.config.SH_PROCESS_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "image/tiff"
            },
            json=payload,
            timeout=120
            )

        response.raise_for_status()

        with rasterio.open(io.BytesIO(response.content)) as src:
            red = src.read(1)
            red_edge = src.read(2)
            nir = src.read(3)
            swir = src.read(4)
            mask = src.read(5)

            rows = src.height
            cols = src.width

        valid = mask > 0

        if not np.any(valid):
            raise RuntimeError(
                "Sentinel-2 returned no valid pixels for the selected field."
            )

        red = np.where(valid, red, np.nan)
        red_edge = np.where(valid, red_edge, np.nan)
        nir = np.where(valid, nir, np.nan)
        swir = np.where(valid, swir, np.nan)

        return {
            "bands": {
                "red": red,
                "red_edge": red_edge,
                "nir": nir,
                "swir": swir
            },
            "grid_bbox": bbox,
            "resolution": (rows, cols),
            "observation_date": today.isoformat(),
            "source": "sentinel-hub"
        }    

    def _fetch_hyperspectral_live(self, point) -> Dict:
        # Integration point for a real hyperspectral BYOC collection
        # (e.g. EnMAP/PRISMA ingested into Sentinel Hub as a custom
        # collection). Left as a stub returning sample data until a
        # collection id + credentials are configured.
        return self._generate_sample_hyperspectral(point, (point[0]-0.01, point[1]-0.01, point[0]+0.01, point[1]+0.01))
