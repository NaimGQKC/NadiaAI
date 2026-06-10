import sqlite3
conn = sqlite3.connect('nadia_ai.db')
conn.row_factory = sqlite3.Row
counts = conn.execute("SELECT use_class, count(*) as count FROM leads GROUP BY use_class").fetchall()
for c in counts:
    print(f"{c['use_class']} | {c['count']}")
conn.close()
