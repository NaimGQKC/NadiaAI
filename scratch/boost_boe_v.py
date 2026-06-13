import sqlite3
import json

def boost():
    conn = sqlite3.connect('nadia_ai.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Identify BOE Section V leads
    cursor.execute("SELECT id, sources FROM leads WHERE source_urls LIKE '%BOE-B%'")
    rows = cursor.fetchall()
    for row in rows:
        sources = json.loads(row['sources'])
        if "BOE (Sec.V)" not in sources:
            # Replace generic BOE with specific label
            new_sources = [s.replace("BOE", "BOE (Sec.V)") if s == "BOE" else s for s in sources]
            if "BOE (Sec.V)" not in new_sources:
                new_sources.append("BOE (Sec.V)")
            cursor.execute("UPDATE leads SET sources = ? WHERE id = ?", (json.dumps(new_sources), row['id']))
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    boost()
