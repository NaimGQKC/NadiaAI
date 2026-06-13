"""Analyze the 2026-06-12 pipeline run results in nadia_ai.db."""
import sqlite3
from collections import Counter

con = sqlite3.connect(r"C:\Users\anaim\OneDrive\Escritorio\NadiaAI\nadia_ai.db")
con.row_factory = sqlite3.Row
cur = con.cursor()

tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("tables:", tables)

t = "leads" if "leads" in tables else tables[0]
cols = [r[1] for r in cur.execute(f"PRAGMA table_info({t})")]
print("cols:", cols)

total = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
print("\nTOTAL ROWS:", total)

# pick likely column names dynamically
def has(c):
    return c in cols

if has("sources"):
    src_counter = Counter()
    for (s,) in cur.execute(f"SELECT sources FROM {t}"):
        if s:
            try:
                import json
                items = json.loads(s) if s.strip().startswith("[") else [s]
            except Exception:
                items = [s]
            for it in items:
                src_counter[it] += 1
    print("\nSOURCES (all-time):")
    for k, v in src_counter.most_common():
        print(f"  {k}: {v}")

for datecol in ("first_seen_at", "fecha_deteccion", "created_at"):
    if has(datecol):
        print(f"\nBY {datecol} (last 10 days):")
        for r in cur.execute(
            f"SELECT substr({datecol},1,10) d, COUNT(*) c FROM {t} GROUP BY d ORDER BY d DESC LIMIT 10"
        ):
            print(f"  {r['d']}: {r['c']}")
        break

if has("tier"):
    print("\nTIER (all-time):")
    for r in cur.execute(f"SELECT tier, COUNT(*) c FROM {t} GROUP BY tier ORDER BY c DESC"):
        print(f"  {r['tier']}: {r['c']}")

# today's rows by source
if has("sources") and has(datecol):
    print("\nTODAY'S ROWS BY SOURCE:")
    src_today = Counter()
    for (s,) in cur.execute(
        f"SELECT sources FROM {t} WHERE substr({datecol},1,10)='2026-06-12'"
    ):
        if s:
            try:
                import json
                items = json.loads(s) if s.strip().startswith("[") else [s]
            except Exception:
                items = [s]
            for it in items:
                src_today[it] += 1
    for k, v in src_today.most_common():
        print(f"  {k}: {v}")

# heir / address / rc fill rates all-time
for c in ("heredero", "heir_name", "heredero_principal", "direccion", "address", "ref_catastral", "catastral_ref"):
    if has(c):
        n = cur.execute(f"SELECT COUNT(*) FROM {t} WHERE {c} IS NOT NULL AND TRIM({c})<>''").fetchone()[0]
        print(f"\nfill {c}: {n}/{total}")
