import sqlite3

c = sqlite3.connect("instance/agrosentinel.db")

queries = [
    ("Maize", "caterpillar"),
    ("Maize", "fall armyworm"),
    ("Maize", "spodoptera"),
    ("Wheat", "caterpillar"),
    ("Wheat", "army worm"),
]

for crop, pest in queries:
    rows = c.execute("""
        SELECT id, insecticide, crop, pest,
               dosage_ai_gm_ha, formulation_dosage, spray_fluid
        FROM pesticide_uses
        WHERE crop_normalized = ?
        AND pest_normalized LIKE ?
    """, (crop.lower(), f"%{pest.lower()}%")).fetchall()

    print(f"\n{crop} + {pest}: {len(rows)} matches")
    for r in rows[:10]:
        print(r)

c.close()