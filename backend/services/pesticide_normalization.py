"""
pesticide_normalization
------------------------
Small, explicit synonym maps used to normalize crop and pest names from
the approved-use pesticide dataset, so a search for crop="Rice",
pest="Brown Plant Hopper" can find a source row printed as "Paddy" /
"BPH" without merging distinct pesticides, dosages, or source rows.

Deliberately conservative: this is a lookup table, not fuzzy matching.
Only pairs an agronomist would consider unambiguous synonyms are listed.
When in doubt, a name is left as its own normalized form (lowercased +
whitespace-collapsed) rather than guessed at -- a missed synonym just
means one fewer alias, never an incorrect merge.

Both scripts/import_pesticide_data.py (writes crop_normalized /
pest_normalized at import time) and
backend/services/pesticide_data_service.py (normalizes the incoming
search term the same way) import from here, so the two sides of the
lookup can never drift apart.
"""

from __future__ import annotations

import re

# crop synonym -> canonical crop name (lowercase)
CROP_SYNONYMS = {
    "paddy": "rice",
    "rice": "rice",
    "bhendi": "okra",
    "okra": "okra",
    "brinjal": "brinjal",
    "eggplant": "brinjal",
    "aubergine": "brinjal",
    "tur": "pigeon pea",
    "arhar": "pigeon pea",
    "red gram": "pigeon pea",
    "pigeonpea": "pigeon pea",
    "pigeon pea": "pigeon pea",
    "bengalgram": "bengal gram",
    "bengal gram": "bengal gram",
    "chick pea": "bengal gram",
    "chickpea": "bengal gram",
    "gram": "bengal gram",
    "green gram": "green gram",
    "black gram": "black gram",
    "tomato": "tomato",
    "cotton": "cotton",
    "chilli": "chilli",
    "chillies": "chilli",
    "chilly": "chilli",
    "maize": "maize",
    "sorghum": "sorghum",
    "jowar": "sorghum",
    "bajra": "pearl millet",
    "pearl millet": "pearl millet",
    "wheat": "wheat",
    "sugarcane": "sugarcane",
    "groundnut": "groundnut",
    "ground nut": "groundnut",
    "peanut": "groundnut",
    "soybean": "soybean",
    "mustard": "mustard",
    "cabbage": "cabbage",
    "cauliflower": "cauliflower",
    "onion": "onion",
    "potato": "potato",
    "grape": "grapes",
    "grapes": "grapes",
    "mango": "mango",
    "apple": "apple",
    "citrus": "citrus",
    "tea": "tea",
    "coffee": "coffee",
    "jute": "jute",
    "sesamum": "sesamum",
    "til": "sesamum",
    "safflower": "safflower",
    "bitter gourd": "bitter gourd",
    "bottle & bitter gourd": "bottle & bitter gourd",
    "cucumber": "cucumber",
    "cucurbit": "cucurbit",
    "gherkins": "gherkins",
    "pomegranate": "pomegranate",
    "rose": "rose",
    "french bean": "french bean",
    "beans": "beans",
    "pea": "pea",
    "turnip": "turnip",
    "radish": "radish",
    "banana": "banana",
    "coconut": "coconut",
    "cardamom": "cardamom",
    "castor": "castor",
    "ber": "ber",
    "fig": "fig",
    "litchi": "litchi",
    "peach": "peach",
    "apricot": "apricot",
    "mandarins": "mandarins",
    "tobacco": "tobacco",
    "teak": "teak",
    "public health": "public health",
    "stored grain": "stored grain",
}

# pest synonym -> canonical pest name (lowercase)
PEST_SYNONYMS = {
    "bph": "brown plant hopper",
    "brown plant hopper": "brown plant hopper",
    "wbph": "white backed plant hopper",
    "white backed plant hopper": "white backed plant hopper",
    "glh": "green leaf hopper",
    "green leaf hopper": "green leaf hopper",
    "white fly": "whitefly",
    "whiteflies": "whitefly",
    "whitefly": "whitefly",
    "white flies": "whitefly",
    "dbm": "diamond back moth",
    "diamond back moth": "diamond back moth",
    "diamond backmoth": "diamond back moth",
    "diamond moth back": "diamond back moth",
    "aphid": "aphid",
    "aphids": "aphid",
    "jassid": "jassid",
    "jassids": "jassid",
    "thrips": "thrips",
    "mite": "mite",
    "mites": "mite",
    "bollworm": "bollworm",
    "bollworms": "bollworm",
    "boll worm": "bollworm",
    "boll worms": "bollworm",
    "pod borer": "pod borer",
    "pod borers": "pod borer",
    "fruit borer": "fruit borer",
    "stem borer": "stem borer",
    "shoot borer": "shoot borer",
    "leaf folder": "leaf folder",
    "leaf roller/folder": "leaf folder",
    "leaf roller": "leaf roller",
}


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def normalize_crop(crop: str) -> str:
    cleaned = _clean(crop)
    return CROP_SYNONYMS.get(cleaned, cleaned)


def normalize_pest(pest: str) -> str:
    cleaned = _clean(pest)
    return PEST_SYNONYMS.get(cleaned, cleaned)