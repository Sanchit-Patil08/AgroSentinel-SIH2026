import io
from datetime import datetime, timedelta, timezone

import requests
import rasterio

from backend.config import Config


# ---------------------------------------------------------
# 1. TEST FIELD
# ---------------------------------------------------------
# Temporary test polygon.
# Later this will come directly from Leaflet.

polygon = [
    [72.85, 19.10],
    [72.86, 19.10],
    [72.86, 19.11],
    [72.85, 19.11],
    [72.85, 19.10],
]


# ---------------------------------------------------------
# 2. OAUTH
# ---------------------------------------------------------

token_response = requests.post(
    Config.SH_TOKEN_URL,
    data={
        "grant_type": "client_credentials",
        "client_id": Config.SH_CLIENT_ID,
        "client_secret": Config.SH_CLIENT_SECRET,
    },
    timeout=20,
)

token_response.raise_for_status()

token = token_response.json()["access_token"]

print("OAuth authentication: SUCCESS")


# ---------------------------------------------------------
# 3. DATE RANGE
# ---------------------------------------------------------

today = datetime.now(timezone.utc).date()
start_date = today - timedelta(days=30)

print("Searching from:", start_date)
print("Searching to  :", today)


# ---------------------------------------------------------
# 4. SENTINEL-2 CROP HEALTH BANDS
# ---------------------------------------------------------
#
# B04 = Red
# B05 = Red Edge
# B08 = NIR
# B11 = SWIR
#
# These are the bands required by our current
# NDVI / NDRE / SAVI / NDMI calculations.
#
# CDSE returns reflectance values in the 0-1 range.
# ---------------------------------------------------------

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


# ---------------------------------------------------------
# 5. PROCESS API PAYLOAD
# ---------------------------------------------------------

payload = {

    "input": {

        "bounds": {

            "geometry": {
                "type": "Polygon",
                "coordinates": [polygon]
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
                        "from": f"{start_date}T00:00:00Z",
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


# ---------------------------------------------------------
# 6. CALL PROCESS API
# ---------------------------------------------------------

url = "https://sh.dataspace.copernicus.eu/process/v1"

response = requests.post(

    url,

    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "image/tiff"
    },

    json=payload,

    timeout=120
)


print()
print("Process API status:", response.status_code)


if not response.ok:

    print("COPERNICUS ERROR:")
    print(response.text)

    raise SystemExit(1)


print("Satellite raster received: YES")
print("Response size:", len(response.content), "bytes")


# ---------------------------------------------------------
# 7. READ RASTER
# ---------------------------------------------------------

with rasterio.open(io.BytesIO(response.content)) as src:

    print()
    print("Raster information")
    print("------------------")

    print("Width:", src.width)
    print("Height:", src.height)
    print("Bands:", src.count)
    print("CRS:", src.crs)
    print("Data type:", src.dtypes)


    # -----------------------------------------------------
    # Read bands
    # -----------------------------------------------------

    red = src.read(1)
    red_edge = src.read(2)
    nir = src.read(3)
    swir = src.read(4)
    mask = src.read(5)


    # -----------------------------------------------------
    # Band statistics
    # -----------------------------------------------------

    print()
    print("Sentinel-2 band statistics")
    print("--------------------------")

    print("B04 Red       mean:", red.mean())
    print("B05 Red Edge  mean:", red_edge.mean())
    print("B08 NIR       mean:", nir.mean())
    print("B11 SWIR      mean:", swir.mean())


    print()
    print("Band ranges")
    print("-----------")

    print("B04:", red.min(), "→", red.max())
    print("B05:", red_edge.min(), "→", red_edge.max())
    print("B08:", nir.min(), "→", nir.max())
    print("B11:", swir.min(), "→", swir.max())


    # -----------------------------------------------------
    # Valid pixels
    # -----------------------------------------------------

    valid_pixels = mask > 0

    print()
    print("Mask information")
    print("----------------")

    print("Mask min:", mask.min())
    print("Mask max:", mask.max())
    print("Valid pixels:", int(valid_pixels.sum()))


    # -----------------------------------------------------
    # NDVI
    # -----------------------------------------------------

    denominator = nir + red
    denominator = denominator + 1e-6

    ndvi = (nir - red) / denominator

    print()
    print("NDVI")
    print("----")

    print("Mean NDVI:", ndvi[valid_pixels].mean())
    print("Min NDVI :", ndvi[valid_pixels].min())
    print("Max NDVI :", ndvi[valid_pixels].max())