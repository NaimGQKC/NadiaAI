import sqlite3
import json

conn = sqlite3.connect('nadia_ai.db')
conn.row_factory = sqlite3.Row

print("=== LEADS WITH PICASSENT IN ADDRESS ===")
rows = conn.execute("SELECT id, causante, sources, source_urls, direccion, localidad, region, tier, ai_extraction_done FROM leads WHERE direccion LIKE '%picassent%'").fetchall()
print(f"Total found: {len(rows)}")
for r in rows[:15]:
    print(f"ID: {r['id']}, Causante: {r['causante']}, Localidad: {r['localidad']}, Region: {r['region']}, Tier: {r['tier']}")
    print(f"  Direccion: {r['direccion']}")
    print(f"  Sources: {r['sources']}")
    print(f"  URLs: {r['source_urls']}")
    print("-" * 50)

print("\n=== TOP CITIES/LOCALITIES FOR ALL LEADS ===")
city_counts = conn.execute("SELECT localidad, COUNT(*) as c FROM leads GROUP BY localidad ORDER BY c DESC LIMIT 20").fetchall()
for c in city_counts:
    print(f"Localidad: {c['localidad']}, Count: {c['c']}")

print("\n=== TOP REGIONS FOR ALL LEADS ===")
region_counts = conn.execute("SELECT region, COUNT(*) as c FROM leads GROUP BY region ORDER BY c DESC LIMIT 20").fetchall()
for r in region_counts:
    print(f"Region: {r['region']}, Count: {r['c']}")

print("\n=== SOURCES FOR ALL LEADS ===")
source_counts = conn.execute("SELECT sources, COUNT(*) as c FROM leads GROUP BY sources ORDER BY c DESC").fetchall()
for s in source_counts:
    print(f"Sources: {s['sources']}, Count: {s['c']}")

conn.close()
