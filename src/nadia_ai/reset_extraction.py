import sqlite3

def reset():
    conn = sqlite3.connect('nadia_ai.db')
    cursor = conn.cursor()
    # Reset if heirs are empty AND (address is empty OR address is a Notary/Court)
    cursor.execute("""
        UPDATE leads 
        SET ai_extraction_done = 0,
            direccion = NULL,
            heir_name = NULL,
            heir_names_json = '[]'
        WHERE (heir_names_json IS NULL OR heir_names_json = '[]' OR heir_names_json = '') 
          AND (
            direccion IS NULL 
            OR direccion = '' 
            OR direccion LIKE '%Notar%' 
            OR direccion LIKE '%Juzgado%' 
            OR direccion LIKE '%Registro%'
          )
          AND ai_extraction_done = 1
    """)
    print(f"Reset {cursor.rowcount} leads for re-extraction.")
    conn.commit()
    conn.close()

if __name__ == "__main__":
    reset()
