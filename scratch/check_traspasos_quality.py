import sqlite3
import sys
import io

# Force UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def check_traspasos():
    conn = sqlite3.connect('nadia_ai.db')
    conn.row_factory = sqlite3.Row
    
    print("--- TRASPASO LEADS SAMPLE ---")
    # Traspasos were ingested with source 'traspasos' or similar
    rows = conn.execute("SELECT id, causante, sources, tier, direccion FROM leads WHERE sources LIKE '%Traspasos%' LIMIT 20").fetchall()
    for r in rows:
        title = r['causante'] or ""
        print(f"ID: {r['id']} | Tier: {r['tier']} | Title: {title[:100]}")

    conn.close()

if __name__ == "__main__":
    check_traspasos()
