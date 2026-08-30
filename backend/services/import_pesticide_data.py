"""
scripts/import_pesticide_data.py
----------------------------------
Imports the "Approved uses of registered insecticides" dataset into the
`pesticide_uses` table (backend/models.py::PesticideUse).

Source data
-----------
Two PDFs were provided, both printing the SAME underlying approved-use
records, just sorted differently:
  - Pesticide_list_pesticide_wise.pdf  (grouped by Insecticide -> Crop -> Pest)
  - Pesticide_list_crop_wise.pdf       (grouped by Crop -> Insecticide -> Pest)

Per the project brief ("avoid duplicate records where possible"), only
ONE of the two is imported -- the pesticide-wise file, because grouping
by insecticide first made it the more reliably table-extractable of the
two with pdfplumber. Importing both would double every record for no
benefit, since they describe the same approved-use data.

How parsing works
------------------
pdfplumber's `extract_table()` already reconstructs the printed grid
per page (it correctly returns None for a cell that's visually merged
with the row above, e.g. when the same Insecticide or Crop repeats for
several pest rows in a row-span). This script:

  1. Reads every page's table in document order.
  2. Forward-fills Insecticide / Crop across None/blank cells --
     including across a page break, since the source table is one
     continuous table split across pages with no repeated header.
  3. Detects "continuation" rows: a row where the dosage/formulation/
     spray-fluid cells are all empty but the pest-name cell has text --
     this happens when a long pest description wraps across a page
     break. That text is appended to the PREVIOUS row's pest name
     rather than created as a new row.
  4. Splits multi-pest cells (e.g. "Stem Borer, Leaf Folder, Plant
     Hoppers") on commas into one row per pest, all sharing the same
     dosage/formulation/spray-fluid figures and the same
     `source_row_group` id, per PesticideUse's docstring.
  5. Normalizes crop/pest names via backend/services/pesticide_normalization.py
     for search, while keeping the ORIGINAL text in `crop` / `pest`.
  6. Skips a row (with a reason) rather than guessing when the
     insecticide or crop is still unknown after forward-filling, or the
     pest text is empty -- malformed rows are reported, never silently
     dropped and never invented.
  7. De-duplicates against what's already in the table (by the
     (insecticide, crop, pest, dosage_ai_gm_ha) unique constraint) so
     the script is safe to re-run.

This importer does NOT invent, adjust, or "clean up" any dosage figure
-- every value stored is copied verbatim from the extracted cell (after
stripping a bare "_" to NULL, since "_" is the source's own "not
specified" marker).

Usage
-----
    python scripts/import_pesticide_data.py
    python scripts/import_pesticide_data.py --source data/pesticide/Pesticide_list_pesticide_wise.pdf
    python scripts/import_pesticide_data.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import sys

# This file lives at backend/services/import_pesticide_data.py -- two
# directories below the repo root -- so three dirname() calls are needed
# to reach the root (services -> backend -> repo root), not two.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)

import pdfplumber

from app import create_app
from backend.extensions import db
from backend.models import PesticideUse
from backend.services.pesticide_normalization import normalize_crop, normalize_pest

DEFAULT_SOURCE = os.path.join(
    _REPO_ROOT, "data", "pesticide", "Pesticide_list_pesticide_wise.pdf",
)

# A tiny number of multi-line insecticide names get cut off by a PDF page
# break, leaving only the formulation-code fragment (e.g. "36 SL")
# visible as the "new" insecticide name in the extracted table -- the
# product name itself ("Monocrotophos") was printed on the previous
# page and pdfplumber has no way to know it still applies. These two
# were found (page 17 of Pesticide_list_pesticide_wise.pdf) and verified
# by cross-referencing the same rows in Pesticide_list_crop_wise.pdf,
# where the full name prints without a page break in the middle. Listed
# explicitly rather than "fixed" with a generic heuristic, so this stays
# a documented, checkable correction instead of a guess.
KNOWN_TRUNCATED_INSECTICIDE_NAMES = {
    "15 SG": "Monocrotophos 15 SG",
    "36 SL": "Monocrotophos 36 SL",
}


def _clean(cell) -> str:
    if cell is None:
        return ""
    return re.sub(r"\s+", " ", str(cell).replace("\n", " ")).strip()


def _is_blank(cell) -> bool:
    return _clean(cell) == ""


def _value_or_none(cell) -> "str | None":
    cleaned = _clean(cell)
    if cleaned in ("", "_"):
        return None
    return cleaned


def extract_raw_rows(source_path: str):
    """Yields dicts: {insecticide, crop, pest, dosage, formulation, spray_fluid,
    page} in document order, with insecticide/crop forward-filled and
    continuation rows merged into the previous row's pest text."""

    rows = []
    last_insecticide = None
    last_crop = None

    with pdfplumber.open(source_path) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            table = page.extract_table()
            if not table:
                continue

            for raw in table:
                if len(raw) < 6:
                    continue
                insecticide_cell, crop_cell, pest_cell, dosage_cell, formulation_cell, spray_cell = raw[:6]

                # Skip the repeated header row (only appears on page 1,
                # but check unconditionally in case a later page ever
                # repeats it too).
                if _clean(insecticide_cell) == "Insecticide" and _clean(crop_cell) == "Crop":
                    continue

                pest_text = _clean(pest_cell)
                dosage_text = _clean(dosage_cell)
                formulation_text = _clean(formulation_cell)
                spray_text = _clean(spray_cell)

                is_continuation = (
                    _is_blank(insecticide_cell)
                    and _is_blank(crop_cell)
                    and pest_text
                    and not dosage_text
                    and not formulation_text
                    and not spray_text
                    and rows
                )

                if is_continuation:
                    # Pest description wrapped across a page/row break --
                    # glue it onto the previous row's pest text.
                    rows[-1]["pest"] = f"{rows[-1]['pest']} {pest_text}".strip()
                    continue

                if not _is_blank(insecticide_cell):
                    cleaned_insecticide = _clean(insecticide_cell)
                    last_insecticide = KNOWN_TRUNCATED_INSECTICIDE_NAMES.get(
                        cleaned_insecticide, cleaned_insecticide
                    )
                if not _is_blank(crop_cell):
                    last_crop = _clean(crop_cell)

                if not pest_text and not dosage_text and not formulation_text and not spray_text:
                    # Fully blank row (rare pdfplumber artifact) -- nothing to record.
                    continue

                rows.append({
                    "insecticide": last_insecticide,
                    "crop": last_crop,
                    "pest": pest_text,
                    "dosage": _value_or_none(dosage_cell),
                    "formulation": _value_or_none(formulation_cell),
                    "spray_fluid": _value_or_none(spray_cell),
                    "page": page_index,
                })

    return rows


def split_pests(pest_text: str):
    """Splits a multi-pest cell on commas. Deliberately does NOT split on
    '&' or '/' -- those typically join words within one pest's common
    name (e.g. 'Shoot & Fruit borer', 'Grey & Stem weevil')."""
    parts = [p.strip(" .") for p in pest_text.split(",")]
    return [p for p in parts if p]


def build_records(raw_rows, source_label: str):
    """Converts raw (possibly multi-pest) rows into individual
    PesticideUse-shaped dicts, plus a list of (row, reason) skips."""

    records = []
    skipped = []

    for i, row in enumerate(raw_rows):
        group_id = f"row-{i}"

        if not row["insecticide"]:
            skipped.append((row, "no insecticide name resolved (even after forward-fill)"))
            continue
        if not row["crop"]:
            skipped.append((row, "no crop name resolved (even after forward-fill)"))
            continue
        if not row["pest"]:
            skipped.append((row, "empty pest name"))
            continue

        pests = split_pests(row["pest"])
        if not pests:
            skipped.append((row, "pest text present but nothing left after split"))
            continue

        for pest in pests:
            records.append({
                "insecticide": row["insecticide"],
                "crop": row["crop"],
                "crop_normalized": normalize_crop(row["crop"]),
                "pest": pest,
                "pest_normalized": normalize_pest(pest),
                "dosage_ai_gm_ha": row["dosage"],
                "formulation_dosage": row["formulation"],
                "spray_fluid": row["spray_fluid"],
                "source": f"{source_label} p.{row['page']}",
                "source_row_group": group_id,
            })

    return records, skipped


def import_records(records, dry_run: bool = False):
    imported = 0
    duplicates = 0

    for rec in records:
        exists = PesticideUse.query.filter_by(
            insecticide=rec["insecticide"],
            crop=rec["crop"],
            pest=rec["pest"],
            dosage_ai_gm_ha=rec["dosage_ai_gm_ha"],
        ).first()
        if exists:
            duplicates += 1
            continue

        if not dry_run:
            db.session.add(PesticideUse(**rec))
        imported += 1

    if not dry_run:
        db.session.commit()

    return imported, duplicates


def main():
    parser = argparse.ArgumentParser(description="Import the approved-use pesticide dataset.")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="Path to the source PDF.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report without writing to the DB.")
    args = parser.parse_args()

    if not os.path.exists(args.source):
        print(f"ERROR: source file not found: {args.source}")
        sys.exit(1)

    source_label = os.path.basename(args.source)
    print(f"Parsing {args.source} ...")
    raw_rows = extract_raw_rows(args.source)
    print(f"  extracted {len(raw_rows)} raw table rows")

    records, skipped = build_records(raw_rows, source_label)
    print(f"  built {len(records)} candidate pesticide-use records (after pest-splitting)")
    print(f"  skipped {len(skipped)} malformed raw rows")
    for row, reason in skipped[:20]:
        print(f"    - SKIPPED ({reason}): {row}")
    if len(skipped) > 20:
        print(f"    ... and {len(skipped) - 20} more (not printed)")

    app = create_app()
    with app.app_context():
        before = PesticideUse.query.count()
        imported, duplicates = import_records(records, dry_run=args.dry_run)
        after = PesticideUse.query.count() if not args.dry_run else before

        print()
        print("---- Import summary ----")
        print(f"  rows already in DB before run : {before}")
        print(f"  new records inserted          : {0 if args.dry_run else (after - before)}")
        print(f"  candidate records that were already present (skipped as duplicates): {duplicates}")
        print(f"  malformed raw rows skipped     : {len(skipped)}")
        print(f"  rows in DB after run           : {after}")
        if args.dry_run:
            print("  (dry run -- nothing was written)")


if __name__ == "__main__":
    main()