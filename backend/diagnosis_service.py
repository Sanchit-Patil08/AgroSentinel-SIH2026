import os
import json
from flask import current_app
from pathlib import Path

from google import genai
from google.genai import types

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

def analyze_diagnosis_image(evidence):
    if not evidence.file_path:
        raise ValueError("No image file found for diagnosis evidence.")

    image_path = Path(current_app.instance_path) / "uploads" / "diagnosis" / evidence.file_path

    if not image_path.exists():
        raise FileNotFoundError(
            f"Diagnosis image not found: {image_path}"
        )

    with open(image_path, "rb") as f:
        image_bytes = f.read()

    prompt = """
You are assisting with crop-health diagnosis.

Analyze the farmer-provided crop image together with the farmer's
description.

Do NOT claim certainty and do NOT pretend to have laboratory-level
identification.

Identify visible evidence such as:
- leaf damage
- chewing damage
- sucking-pest symptoms
- leaf curling
- wilting
- spots or lesions
- discoloration
- insect presence
- fungal-like symptoms
- bacterial-like symptoms
- nutrient/environmental stress indicators
- anything else visibly relevant

If an exact pest or disease cannot be reliably identified, say so.

Return ONLY valid JSON using this structure:

{
  "visual_observation": "What is visibly present in the image",

  "pest_detection": {
    "detected": true,
    "name": "Name of the visible pest if reasonably identifiable, otherwise null",
    "category": "chewing_pest | sucking_pest | other_pest | unknown",
    "confidence": 0.0
  },

  "disease_detection": {
    "detected": false,
    "name": "Name of the possible disease if reasonably identifiable, otherwise null",
    "category": "fungal | bacterial | viral | other | unknown",
    "confidence": 0.0
  },

  "damage_pattern": "chewing | sucking | curling | wilting | leaf_spot | discoloration | not_sure",

  "severity": "low | moderate | high | unknown",

  "possible_causes": [
    "possible cause 1",
    "possible cause 2"
  ],

  "confidence": 0.0,

  "verification": [
    "What the farmer should check in the field"
  ],

  "image_quality": "good | acceptable | poor"
}

Confidence must be between 0 and 1.
Do not give pesticide dosage or chemical treatment instructions.
"""

    farmer_context = f"""
Image type: {evidence.image_type}
Farmer-selected damage pattern: {evidence.damage_pattern or "not provided"}
Farmer note: {evidence.note or "No additional note"}
"""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Part.from_bytes(
                data=image_bytes,
                mime_type=evidence.content_type or "image/jpeg",
            ),
            prompt + "\n\nFarmer context:\n" + farmer_context,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )

    result = json.loads(response.text)

    return result