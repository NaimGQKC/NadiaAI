import sqlite3
conn = sqlite3.connect('nadia_ai.db')
cursor = conn.cursor()
cursor.execute("SELECT COUNT(*) FROM leads WHERE heir_name != ''")
print(f"Leads with heirs: {cursor.fetchone()[0]}")
conn.close()
