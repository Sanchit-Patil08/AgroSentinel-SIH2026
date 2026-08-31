"""
Gemini service for AgroSentinel's standalone Pest & Disease Detection tool.

This service is deliberately independent from field analysis, weather,
satellite, IoT, risk, and pesticide database workflows.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import requests

from backend.config import Config


GEMINI_MODEL = "gemini-2.5-flash"

GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

MAX_IMAGE_BYTES = 8 * 1024 * 1024

ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}


def clean(value: Any, max_len: int = 1000) -> str:
    return str(value or "").strip()[:max_len]


def build_prompt(data: dict[str, str]) -> str:
    return f"""
You are AgroSentinel's farmer-facing AI assistant for identifying possible
crop pests and diseases.

This is a standalone one-off advisory tool.

It is NOT connected to:
- saved field analysis
- Sentinel satellite data
- weather history
- IoT sensors
- AgroSentinel's risk engine
- pesticide treatment records

Analyze the farmer's observations and, when provided, the plant photo.

IMPORTANT RULES:

1. Do not claim certainty from symptoms alone.
2. Give a ranked differential diagnosis.
3. If evidence is insufficient, clearly say that more inspection is needed.
4. Use simple, farmer-friendly language suitable for an Indian farmer.
5. Do NOT prescribe a pesticide product, dosage, tank mix,
   concentration, or spray schedule.
6. Give safe non-chemical actions the farmer can take immediately.
7. Explain useful signs the farmer should inspect.
8. Mention when local agricultural expert confirmation is appropriate.
9. Never invent sources, studies, laboratory results, or certainty.
10. A photo may be unclear. Account for image limitations.
11. Consider pest, disease, nutrient deficiency, environmental stress,
    herbicide injury, or other causes where appropriate.
12. Return ONLY valid JSON.
13. Do not use markdown fences.

Return exactly this structure:

{{
  "likely_problem": "string",
  "problem_type": "pest | disease | nutrient | environmental | unknown",
  "confidence": "low | medium | high",
  "confidence_percent": 0,

  "what_it_may_be": [
    {{
      "name": "string",
      "likelihood": "high | medium | low",
      "reason": "short farmer-friendly reason"
    }}
  ],

  "what_you_are_seeing": [
    "short interpretation of the farmer's observations/photo"
  ],

  "signs_to_check": [
    "specific things to inspect on the plant or nearby plants"
  ],

  "what_to_do_now": [
    "safe immediate action"
  ],

  "avoid_for_now": [
    "action to avoid until diagnosis is confirmed"
  ],

  "when_to_get_help": [
    "condition where local expert confirmation is recommended"
  ],

  "note": "short AI-assistance disclaimer"
}}

FARMER INPUT

Crop: {data["crop"]}
Crop stage: {data["stage"]}
Plant part affected: {data["plant_part"]}
Appearance: {data["appearance"]}
Spread: {data["spread"]}
Speed: {data["speed"]}
Farmer notes: {data["notes"]}
""".strip()


def _extract_json(text: str) -> dict:
    """
    Extract JSON from Gemini's response.

    Handles:
    - normal JSON
    - JSON wrapped in markdown fences
    - small amounts of surrounding text
    """

    text = (text or "").strip()

    if text.startswith("```"):
        text = text.strip("`").strip()

        if text.lower().startswith("json"):
            text = text[4:].strip()

    try:
        parsed = json.loads(text)

        if isinstance(parsed, dict):
            return parsed

    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if start >= 0 and end > start:
        parsed = json.loads(text[start:end + 1])

        if isinstance(parsed, dict):
            return parsed

    raise ValueError(
        "Gemini returned an invalid diagnosis format."
    )


def detect(
    data: dict[str, str],
    image_bytes: bytes | None = None,
    image_mime_type: str | None = None,
) -> dict:

    api_key = (Config.GEMINI_API_KEY or "").strip()

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not configured. "
            "Add it to .env and restart AgroSentinel."
        )

    parts = [
        {
            "text": build_prompt(data)
        }
    ]

    if image_bytes:

        if (
            not image_mime_type
            or image_mime_type not in ALLOWED_IMAGE_TYPES
        ):
            raise ValueError(
                "Photo must be JPG, PNG, or WEBP."
            )

        if len(image_bytes) > MAX_IMAGE_BYTES:
            raise ValueError(
                "Photo must be 8 MB or smaller."
            )

        parts.append(
            {
                "inline_data": {
                    "mime_type": image_mime_type,
                    "data": base64.b64encode(
                        image_bytes
                    ).decode("ascii"),
                }
            }
        )

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": parts,
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }

    response = requests.post(
        GEMINI_URL,
        params={"key": api_key},
        json=payload,
        timeout=60,
    )

    if not response.ok:

        try:
            detail = (
                response.json()
                .get("error", {})
                .get("message")
            )

        except Exception:
            detail = None

        raise RuntimeError(
            detail
            or f"Gemini request failed ({response.status_code})."
        )

    body = response.json()

    candidates = body.get("candidates") or []

    if not candidates:
        raise RuntimeError(
            "Gemini did not return a diagnosis."
        )

    output_parts = (
        candidates[0]
        .get("content", {})
        .get("parts", [])
    )

    text = "".join(
        part.get("text", "")
        for part in output_parts
        if part.get("text")
    )

    return _extract_json(text)