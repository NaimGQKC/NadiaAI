import sqlite3
conn = sqlite3.connect('nadia_ai.db')
cursor = conn.cursor()
cursor.execute("SELECT COUNT(*) FROM leads")
print(f"Total leads: {cursor.fetchone()[0]}")
conn.close()
