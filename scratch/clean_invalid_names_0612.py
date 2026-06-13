"""One-off cleanup (2026-06-12): purge corrupt lead 2534 and invalid heir names.

- Lead 2534: BOE Sec.V regex disaster (heirs = legal boilerplate, no causante,
  direccion = announcement title, sole Tier A in DB). Deleted.
- Any lead whose heir_name / heir_names_json entries fail person-name
  validation gets them cleaned; if all heirs were junk, extraction is
  re-queued (ai_extraction_done = 0).
"""
import json
import sqlite3
import sys

sys.path.insert(0, r"C:\Users\anaim\OneDrive\Escritorio\NadiaAI\src")
sys.stdout.reconfigure(encoding="utf-8")

from nadia_ai.utils.names import clean_name_list, is_valid_person_name

con = sqlite3.connect(r"C:\Users\anaim\OneDrive\Escritorio\NadiaAI\nadia_ai.db")
con.row_factory = sqlite3.Row
cur = con.cursor()

# 1. Delete corrupt lead 2534
row = cur.execute("SELECT id, causante, heir_names_json, tier FROM leads WHERE id=2534").fetchone()
if row:
    print("Deleting lead 2534:", dict(row))
    cur.execute("DELETE FROM lead_edicts WHERE lead_id=2534")
    cur.execute("DELETE FROM leads WHERE id=2534")
else:
    print("Lead 2534 already gone")

# 2. Clean invalid heir names everywhere
rows = cur.execute(
    "SELECT id, heir_name, heir_names_json FROM leads "
    "WHERE (heir_name IS NOT NULL AND TRIM(heir_name)<>'') "
    "   OR (heir_names_json IS NOT NULL AND heir_names_json NOT IN ('', '[]'))"
).fetchall()

cleaned = 0
requeued = 0
for r in rows:
    try:
        heirs = json.loads(r["heir_names_json"] or "[]")
    except json.JSONDecodeError:
        heirs = []
    if r["heir_name"] and r["heir_name"] not in heirs:
        heirs.append(r["heir_name"])

    valid = clean_name_list(heirs)
    if valid == heirs and (not r["heir_name"] or is_valid_person_name(r["heir_name"])):
        continue

    had_any = bool(heirs)
    new_primary = valid[0] if valid else None
    requeue = 1 if (had_any and not valid) else 0
    cur.execute(
        "UPDATE leads SET heir_name=?, heir_names_json=?, "
        "ai_extraction_done=CASE WHEN ? = 1 THEN 0 ELSE ai_extraction_done END "
        "WHERE id=?",
        (new_primary, json.dumps(valid), requeue, r["id"]),
    )
    cleaned += 1
    requeued += requeue
    print(f"  lead {r['id']}: {heirs} -> {valid}")

con.commit()
print(f"\nCleaned {cleaned} leads, re-queued {requeued} for extraction")

# 3. Report (don't touch) suspicious causantes
bad_causantes = [
    (r["id"], r["causante"])
    for r in cur.execute("SELECT id, causante FROM leads WHERE causante IS NOT NULL AND TRIM(causante)<>''")
    if not is_valid_person_name(r["causante"])
]
print(f"\nSuspicious causantes (reported only): {len(bad_causantes)}")
for lid, c in bad_causantes[:20]:
    print(f"  {lid}: {c!r}")
print("tierA now:", cur.execute("SELECT COUNT(*) FROM leads WHERE tier='A'").fetchone()[0])
con.close()
