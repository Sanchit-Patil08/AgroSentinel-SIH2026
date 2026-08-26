import numpy as np

from backend.services.satellite_service import SatelliteService
from backend.services.spectral_analysis import compute_all_indices


service = SatelliteService()

polygon = [
    [72.85, 19.10],
    [72.86, 19.10],
    [72.86, 19.11],
    [72.85, 19.11],
    [72.85, 19.10]
]

bbox = (
    min(p[0] for p in polygon),
    min(p[1] for p in polygon),
    max(p[0] for p in polygon),
    max(p[1] for p in polygon)
)

print("Fetching Sentinel-2 data...")

satellite_data = service.get_multispectral_bands(
    polygon,
    bbox
)

print("Source:", satellite_data["source"])
print("Observation date:", satellite_data["observation_date"])

bands = satellite_data["bands"]

indices = compute_all_indices(bands)

print()
print("Spectral indices")
print("----------------")

for name, values in indices.items():

    valid = values[np.isfinite(values)]

    print(
        f"{name.upper()}: "
        f"mean={valid.mean():.4f}, "
        f"min={valid.min():.4f}, "
        f"max={valid.max():.4f}"
    )