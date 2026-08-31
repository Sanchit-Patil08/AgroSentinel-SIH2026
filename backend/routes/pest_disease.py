"""
Standalone Pest & Disease Detection routes.

This route does not create database records and does not modify
the existing field-analysis workflow.
"""

from __future__ import annotations

import logging

import requests
from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required

from backend.services.pest_disease_service import (
    ALLOWED_IMAGE_TYPES,
    MAX_IMAGE_BYTES,
    clean,
    detect,
)


logger = logging.getLogger(__name__)

pest_disease_bp = Blueprint(
    "pest_disease",
    __name__,
)


@pest_disease_bp.get("/pest-disease")
@login_required
def pest_disease_page():
    return render_template(
        "pest_disease.html"
    )


@pest_disease_bp.post("/api/pest-disease/detect")
@login_required
def detect_pest_disease():

    data = {
        "crop": clean(
            request.form.get("crop"),
            100,
        ),

        "stage": clean(
            request.form.get("stage"),
            100,
        ),

        "plant_part": clean(
            request.form.get("plant_part"),
            150,
        ),

        "appearance": clean(
            request.form.get("appearance"),
            1200,
        ),

        "spread": clean(
            request.form.get("spread"),
            150,
        ),

        "speed": clean(
            request.form.get("speed"),
            150,
        ),

        "notes": clean(
            request.form.get("notes"),
            1500,
        ),
    }

    required = {
        "crop": "Please select the crop.",
        "appearance": (
            "Please describe what you are seeing "
            "on the plant."
        ),
    }

    for key, message in required.items():

        if not data[key]:
            return jsonify({
                "error": message
            }), 400

    image_bytes = None
    image_mime_type = None

    image = request.files.get("photo")

    if image and image.filename:

        image_mime_type = (
            image.mimetype or ""
        ).lower()

        if image_mime_type not in ALLOWED_IMAGE_TYPES:
            return jsonify({
                "error": (
                    "Photo must be JPG, PNG, or WEBP."
                )
            }), 400

        image_bytes = image.read(
            MAX_IMAGE_BYTES + 1
        )

        if len(image_bytes) > MAX_IMAGE_BYTES:
            return jsonify({
                "error": (
                    "Photo must be 8 MB or smaller."
                )
            }), 400

    try:

        result = detect(
            data,
            image_bytes=image_bytes,
            image_mime_type=image_mime_type,
        )

    except requests.RequestException:

        logger.exception(
            "Gemini network error in pest/disease tool"
        )

        return jsonify({
            "error": (
                "Could not reach the AI service. "
                "Please try again."
            )
        }), 502

    except (RuntimeError, ValueError) as exc:

        logger.exception(
            "Pest/disease diagnosis error"
        )

        return jsonify({
            "error": str(exc)
        }), 502

    except Exception:

        logger.exception(
            "Unexpected pest/disease detection error"
        )

        return jsonify({
            "error": (
                "Something went wrong while "
                "preparing the diagnosis."
            )
        }), 500

    return jsonify({
        "success": True,
        "result": result,
        "photo_used": bool(image_bytes),
    })