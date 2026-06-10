import sqlite3
import json

def check():
    conn = sqlite3.connect('nadia_ai.db')
    conn.row_factory = sqlite3.Row
    
    print("--- LEAD DISTRIBUTION ---")
    rows = conn.execute("SELECT tier, COUNT(*) as count FROM leads GROUP BY tier").fetchall()
    for r in rows:
        print(f"Tier {r['tier']}: {r['count']}")
    
    print("\n--- RECENT TIER A LEADS ---")
    rows = conn.execute("SELECT id, causante, direccion, heir_name, first_seen_at FROM leads WHERE tier = 'A' ORDER BY first_seen_at DESC LIMIT 10").fetchall()
    for r in rows:
        print(f"ID: {r['id']} | {r['causante']} | {r['direccion']} | Heir: {r['heir_name']}")

    print("\n--- AI EXTRACTION STATS ---")
    count = conn.execute("SELECT COUNT(*) FROM leads WHERE ai_extraction_done = 1").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    print(f"AI Processed: {count} / {total}")

    conn.close()

if __name__ == "__main__":
    check()
