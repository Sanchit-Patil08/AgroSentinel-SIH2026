import requests
from datetime import datetime, timedelta

from backend.config import Config


# Example field polygon.
# IMPORTANT: coordinates are [longitude, latitude] for GeoJSON.
polygon = [
    [72.85, 19.10],
    [72.86, 19.10],
    [72.86, 19.11],
    [72.85, 19.11],
    [72.85, 19.10],
]

today = datetime.utcnow().date()
start = today - timedelta(days=30)

payload = {
    "collections": ["sentinel-2-l2a"],
    "datetime": f"{start}T00:00:00Z/{today}T23:59:59Z",
    "intersects": {
        "type": "Polygon",
        "coordinates": [polygon]
    },
    "limit": 5
}


# Get OAuth token
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


# Search Sentinel-2 catalog
response = requests.post(
    Config.SH_CATALOG_URL,
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    },
    json=payload,
    timeout=30,
)

print("Catalog status:", response.status_code)

if response.ok:
    data = response.json()

    features = data.get("features", [])

    print("Scenes found:", len(features))

    for feature in features:
        print(
            "ID:",
            feature.get("id"),
            "| Date:",
            feature.get("properties", {}).get("datetime")
        )
else:
    print(response.text)