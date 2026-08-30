"""
pesticide_data_service
------------------------
The ONE query layer over the PesticideUse table (backend/models.py).

Both the field-specific Intervention Engine
(backend/services/intervention_engine.py) and the future/general
Pesticide Advisor tool (currently a disabled "Soon" nav item -- see
templates/dashboard.html) are meant to call `search_pesticides()` below
rather than each rolling their own query. Per the project brief: "Do not
create a second duplicate pesticide database" and "Create reusable
service/query functions so it can use the same dataset later."

This module does no LLM reasoning and never invents a record -- it is a
thin, deterministic filter over PesticideUse rows. Every result traces
back to an approved-use dataset row via `source`.
"""

from __future__ import annotations

from typing import List, Optional

from backend.models import PesticideUse
from backend.services.pesticide_normalization import normalize_crop, normalize_pest


def search_pesticides(
    crop: Optional[str] = None,
    pest: Optional[str] = None,
    insecticide: Optional[str] = None,
    limit: int = 50,
) -> List[PesticideUse]:
    """Filters PesticideUse rows by crop / pest / insecticide.

    Matching is normalized-name based for crop/pest (so "Rice" also
    matches source rows printed as "Paddy", "BPH" also matches "Brown
    Plant Hopper", etc. -- see pesticide_normalization.py) and a
    case-insensitive substring match for insecticide (free-text product
    name lookup). Any parameter left as None is not filtered on. Always
    returns real PesticideUse rows -- never a generated/paraphrased
    recommendation.
    """

    query = PesticideUse.query

    if crop:
        query = query.filter(PesticideUse.crop_normalized == normalize_crop(crop))

    if pest:
        query = query.filter(PesticideUse.pest_normalized == normalize_pest(pest))

    if insecticide:
        query = query.filter(PesticideUse.insecticide.ilike(f"%{insecticide.strip()}%"))

    return (
        query.order_by(PesticideUse.insecticide.asc(), PesticideUse.crop.asc())
        .limit(limit)
        .all()
    )


def search_pesticides_any_pest(
    crop: Optional[str],
    pest_candidates: List[str],
    limit: int = 25,
) -> List[PesticideUse]:
    """Convenience wrapper used by the Intervention Engine: a diagnosis
    rarely yields one exact pest name, more often a short list of
    plausible pest names for a given damage pattern (e.g. "sucking"
    damage -> aphids / thrips / whiteflies / jassids). Tries each
    candidate against the given crop and returns the union, de-duplicated
    by PesticideUse.id, still capped at `limit` and still fully traceable
    to real dataset rows.
    """

    seen = {}
    for candidate in pest_candidates:
        for row in search_pesticides(crop=crop, pest=candidate, limit=limit):
            seen[row.id] = row
    return list(seen.values())[:limit]


def distinct_crops() -> List[str]:
    """All distinct original (non-normalized) crop names in the dataset,
    for populating a future Pesticide Advisor crop dropdown."""
    rows = PesticideUse.query.with_entities(PesticideUse.crop).distinct().order_by(PesticideUse.crop).all()
    return [r[0] for r in rows]


def dataset_size() -> int:
    return PesticideUse.query.count()