import sqlite3
conn = sqlite3.connect('nadia_ai.db')
cursor = conn.cursor()
cursor.execute("UPDATE leads SET region = 'Zaragoza' WHERE region = 'Desconocida' OR region = '' OR region IS NULL")
print(f"Updated {cursor.rowcount} rows to Zaragoza")
conn.commit()
conn.close()
