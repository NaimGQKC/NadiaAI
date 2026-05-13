import sqlite3
conn = sqlite3.connect('nadia_ai.db')
conn.execute("UPDATE edicts SET source_url = REPLACE(source_url, '/document', '') WHERE source = 'tablon'")
conn.commit()
