import os
import json
from pathlib import Path

from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

image_path = Path("instance/uploads/diagnosis/test.jpg")

if not image_path.exists():
    raise FileNotFoundError(f"Image not found: {image_path}")

with open(image_path, "rb") as f:
    image_bytes = f.read()

prompt = """
You are assisting with crop-health diagnosis.

Analyze the farmer-provided crop image.

Do not claim certainty and do not pretend to have laboratory-level identification.

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

For pest_detection:
- Set detected to true only if an insect or pest is actually visible or there is strong visible evidence of pest activity.
- If a pest is visible but cannot be reliably identified, use a broad name such as "caterpillar" or "chewing insect".
- Do not claim exact species identification unless the image provides reasonably strong visual evidence.
- Confidence represents confidence in the visual identification.

For disease_detection:
- Set detected to true only when visible symptoms are reasonably consistent with a disease.
- Do not diagnose a disease solely from field stress indicators that are not visible in the image.

For severity:
- Estimate only the visible severity in the uploaded image.
- Use "unknown" when severity cannot be judged reliably.

Return ONLY valid JSON using this structure:

{
  "visual_observation": "What is visibly present in the image",

  "pest_detection": {
    "detected": true,
    "name": "Name of visible pest if reasonably identifiable, otherwise null",
    "category": "chewing_pest | sucking_pest | other_pest | unknown",
    "confidence": 0.0
  },

  "disease_detection": {
    "detected": false,
    "name": "Name of possible disease if reasonably identifiable, otherwise null",
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

The overall confidence must be between 0 and 1.

Do not provide pesticide dosage or chemical treatment instructions.

The result is a preliminary visual assessment and must not be treated as laboratory confirmation.
"""

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=[
        types.Part.from_bytes(
            data=image_bytes,
            mime_type="image/jpeg",
        ),
        prompt,
    ],
    config=types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.2,
    ),
)

result = json.loads(response.text)

print("\n========== GEMINI IMAGE ANALYSIS ==========\n")
print(json.dumps(result, indent=2))