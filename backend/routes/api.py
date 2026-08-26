from flask import Blueprint, jsonify, request

from backend.services.field_analyzer import analyze_field

api_bp = Blueprint("api", __name__, url_prefix="/api")

CROP_TYPES = ["Wheat", "Rice", "Cotton", "Maize", "Sugarcane", "Soybean"]
CROP_STAGES = ["Sowing", "Vegetative", "Flowering", "Maturity", "Harvest"]


@api_bp.get("/config")
def get_config():
    """Static form options for the demo UI."""
    return jsonify({"crop_types": CROP_TYPES, "crop_stages": CROP_STAGES})


@api_bp.post("/analyze")
def analyze():
    payload = request.get_json(force=True, silent=True) or {}

    polygon = payload.get("polygon")
    crop_type = payload.get("crop_type")
    crop_stage = payload.get("crop_stage")

    if not polygon or len(polygon) < 3:
        return jsonify({"error": "A valid field polygon (>=3 points) is required."}), 400
    if not crop_type or not crop_stage:
        return jsonify({"error": "crop_type and crop_stage are required."}), 400

    # Ensure the polygon ring is closed for shapely
    if polygon[0] != polygon[-1]:
        polygon = polygon + [polygon[0]]

    try:
        result = analyze_field(polygon, crop_type, crop_stage)
    except Exception as exc:  # noqa: BLE001 - surface a clean error to the demo UI
        return jsonify({"error": f"Analysis failed: {exc}"}), 500

    return jsonify(result)