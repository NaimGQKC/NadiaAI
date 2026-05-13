import sqlite3
conn = sqlite3.connect('nadia_ai.db')
conn.execute("UPDATE leads SET heir_names_json='[]', heir_name=NULL, date_of_death=NULL")
conn.commit()
print("Reset successful")
