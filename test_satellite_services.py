from backend.services.satellite_service import SatelliteService

service = SatelliteService()

polygon = [
    [72.82, 19.10],
    [72.83, 19.10],
    [72.83, 19.11],
    [72.82, 19.11],
    [72.82, 19.10]
]

bbox = (
    min(p[0] for p in polygon),
    min(p[1] for p in polygon),
    max(p[0] for p in polygon),
    max(p[1] for p in polygon)
)

print("Testing SatelliteService...")
print("USE_SAMPLE_DATA:", service.config.USE_SAMPLE_DATA)

data = service.get_multispectral_bands(polygon, bbox)

print("\nSource:", data["source"])
print("Observation date:", data["observation_date"])
print("Resolution:", data["resolution"])

for name, band in data["bands"].items():
    print(
        f"{name}: "
        f"shape={band.shape}, "
        f"mean={band.mean():.4f}, "
        f"min={band.min():.4f}, "
        f"max={band.max():.4f}"
    )