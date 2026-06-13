import sqlite3
import json

conn = sqlite3.connect('nadia_ai.db')
conn.row_factory = sqlite3.Row

print("=== LEADS WITH 'TANATORIO' OR SIMILAR IN ADDRESS ===")
rows = conn.execute("""
    SELECT id, causante, direccion, localidad, sources, tier 
    FROM leads 
    WHERE direccion LIKE '%tanatorio%' 
       OR direccion LIKE '%crematorio%' 
       OR direccion LIKE '%cementerio%' 
       OR direccion LIKE '%velatorio%' 
       OR direccion LIKE '%funeraria%'
""").fetchall()

print(f"Total found: {len(rows)}")
for r in rows[:20]:
    print(f"ID: {r['id']}, Causante: {r['causante']}, Localidad: {r['localidad']}, Tier: {r['tier']}")
    print(f"  Direccion: {r['direccion']}")
    print("-" * 50)

print("\n=== UNIQUE ADDRESSES WITH MULTIPLE LEADS ===")
duplicates = conn.execute("""
    SELECT direccion, localidad, COUNT(*) as c, GROUP_CONCAT(tier) as tiers
    FROM leads 
    WHERE direccion IS NOT NULL AND direccion != ''
    GROUP BY direccion, localidad
    HAVING c > 1
    ORDER BY c DESC
""").fetchall()
for d in duplicates[:15]:
    print(f"Count: {d['c']}, Tiers: {d['tiers']}, Localidad: {d['localidad']}")
    print(f"  Direccion: {d['direccion']}")
    print("-" * 50)

conn.close()
