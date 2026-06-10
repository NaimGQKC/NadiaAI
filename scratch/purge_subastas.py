import sqlite3
import json

conn = sqlite3.connect('nadia_ai.db')
cursor = conn.cursor()

# Find all leads with 'subastas' in their sources JSON array
cursor.execute("SELECT id, sources FROM leads")
rows = cursor.fetchall()

to_delete = []
for row_id, sources_json in rows:
    try:
        sources = json.loads(sources_json or "[]")
        if "subastas" in sources or "subastas_leads" in sources:
            to_delete.append(row_id)
    except:
        pass

if to_delete:
    placeholders = ",".join(["?"] * len(to_delete))
    cursor.execute(f"DELETE FROM leads WHERE id IN ({placeholders})", to_delete)
    print(f"Deleted {len(to_delete)} subastas leads.")
else:
    print("No subastas leads found to delete.")

conn.commit()
conn.close()
