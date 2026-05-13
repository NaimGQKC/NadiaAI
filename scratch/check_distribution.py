import sqlite3
conn = sqlite3.connect('nadia_ai.db')
cursor = conn.cursor()
cursor.execute("SELECT tier, ai_extraction_done, COUNT(*) FROM leads GROUP BY tier, ai_extraction_done")
for row in cursor.fetchall():
    print(row)
conn.close()
