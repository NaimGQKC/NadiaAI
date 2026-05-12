import sqlite3
import json

def check_links():
    conn = sqlite3.connect('nadia_ai.db')
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, causante, source_urls FROM leads WHERE source_urls IS NOT NULL LIMIT 20").fetchall()
    
    for row in rows:
        urls = json.loads(row['source_urls'])
        print(f"Lead {row['id']} ({row['causante']}): {urls}")
    
    conn.close()

if __name__ == "__main__":
    check_links()
