import sqlite3
import json

conn = sqlite3.connect('nadia_ai.db')
conn.row_factory = sqlite3.Row

print("=== SAMPLES OF TIER A LEADS ===")
rows = conn.execute("SELECT id, causante, direccion, localidad, region, sources, first_seen_at FROM leads WHERE tier = 'A'").fetchall()
print(f"Total Tier A leads: {len(rows)}")
for r in rows[:15]:
    print(f"ID: {r['id']}, Causante: {r['causante']}, Localidad: {r['localidad']}, Region: {r['region']}")
    print(f"  Direccion: {r['direccion']}")
    print(f"  Sources: {r['sources']}")
    print(f"  Date: {r['first_seen_at']}")
    print("-" * 50)

conn.close()
