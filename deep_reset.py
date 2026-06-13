import sqlite3

def reset():
    conn = sqlite3.connect('nadia_ai.db')
    conn.execute("""
        UPDATE leads 
        SET ai_extraction_done = 0, 
            heir_names_json = '[]', 
            heir_name = NULL, 
            direccion = NULL, 
            referencia_catastral = NULL
    """)
    conn.commit()
    print("Database deep reset complete. 1,161 leads ready for fresh extraction.")
    conn.close()

if __name__ == "__main__":
    reset()
