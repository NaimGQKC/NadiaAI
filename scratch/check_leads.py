from nadia_ai.db import get_connection
import sqlite3
import json

conn = get_connection()
conn.row_factory = sqlite3.Row
r = conn.execute('SELECT count(*) FROM leads').fetchone()
print(f"Leads: {r[0]}")
r = conn.execute('SELECT subsource, count(*) FROM leads GROUP BY subsource').fetchall()
for x in r:
    print(f"{x['subsource']}: {x[1]}")

print("\nRaw Edicts by Source:")
r = conn.execute('SELECT source, count(*) FROM edicts GROUP BY source').fetchall()
for x in r:
    print(f"{x['source']}: {x[1]}")
r = conn.execute('SELECT count(*) FROM leads WHERE ai_extraction_done=0').fetchone()
print(f"Pending extraction: {r[0]}")

# Sample a few leads to see if heirs are being extracted
print("\nSample Leads with Heirs:")
r = conn.execute('SELECT causante, heir_names_json, source_urls FROM leads WHERE heir_names_json IS NOT NULL AND heir_names_json != "[]" LIMIT 5').fetchall()
for x in r:
    print(f"Causante: {x['causante']}")
    print(f"Heirs: {x['heir_names_json']}")
    print(f"URL: {x['source_urls']}")
    print("-" * 20)

conn.close()
