import sqlite3
import json

conn = sqlite3.connect('nadia_ai.db')
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT id, causante, sources, first_seen_at FROM leads WHERE first_seen_at >= date('now', '-7 days')").fetchall()

print(f"{'ID':<5} | {'Causante':<30} | {'Sources':<20} | {'Date'}")
print("-" * 80)
for r in rows:
    sources = json.loads(r['sources'] or "[]")
    print(f"{r['id']:<5} | {str(r['causante']):<30} | {', '.join(sources):<20} | {r['first_seen_at']}")
conn.close()
