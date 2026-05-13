import sqlite3
conn = sqlite3.connect('nadia_ai.db')
cursor = conn.cursor()
cursor.execute("PRAGMA table_info(leads)")
for col in cursor.fetchall():
    print(col)
conn.close()
