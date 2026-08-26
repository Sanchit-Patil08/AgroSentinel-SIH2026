from backend.services.satellite_service import SatelliteService
from backend.services.spectral_analysis import compute_all_indices
from backend.services.zone_processor import build_field_zones


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

print("\nCalculating spectral indices...")

indices = compute_all_indices(bands)

print("NDVI mean:", indices["ndvi"].mean())
print("NDRE mean:", indices["ndre"].mean())
print("SAVI mean:", indices["savi"].mean())
print("NDMI mean:", indices["ndmi"].mean())

print("\nBuilding field zones...")

result = build_field_zones(
    polygon,
    satellite_data,
    indices
)

print("Total field area:", result["total_area_ha"], "ha")
print("Zones created:", len(result["zones"]))

print("\nZone information")
print("----------------")

for zone in result["zones"]:
    print(
        f"Zone {zone['zone_id']}: "
        f"area={zone['area_ha']} ha, "
        f"NDVI={zone['ndvi']}, "
        f"NDRE={zone['ndre']}, "
        f"SAVI={zone['savi']}, "
        f"NDMI={zone['ndmi']}, "
        f"health={zone['health_status']}"
    )