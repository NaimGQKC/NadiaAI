import sqlite3
conn = sqlite3.connect('nadia_ai.db')
# Tag all existing Tablón leads as Zaragoza (since that scraper is Zaragoza-only)
conn.execute("UPDATE leads SET region = 'Zaragoza' WHERE region = '' OR region IS NULL")
conn.commit()
print("Tagged existing leads as Zaragoza")

# Reset heirs for re-extraction
conn.execute("UPDATE leads SET heir_names_json='[]', heir_name='', date_of_death=''")
conn.commit()
print("Reset heirs for re-extraction")

# Count
count = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
print(f"Total leads in DB: {count}")
conn.close()
