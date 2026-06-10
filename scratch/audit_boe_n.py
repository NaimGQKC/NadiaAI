import sqlite3
import requests
import re

def audit_boe_n():
    conn = sqlite3.connect('nadia_ai.db')
    cursor = conn.cursor()
    # Select leads from BOE-N for May 12th
    cursor.execute("SELECT source_id, source_url FROM edicts WHERE source='boe_boe_n' AND published_at LIKE '2026-05-12%'")
    rows = cursor.fetchall()
    conn.close()

    print(f"Auditing {len(rows)} leads from BOE-N (May 12)...")
    
    real_count = 0
    noise_count = 0
    
    for doc_id, url in rows:
        try:
            r = requests.get(url, timeout=10)
            text = r.text.lower()
            
            # Identify if it's a real inheritance proceeding or just a registry notice
            if "declaración de herederos" in text or "abintestato" in text or "sucesión intestada" in text:
                real_count += 1
                print(f"[REAL] {doc_id} - {url}")
            elif "registro de la propiedad" in text or "colindantes" in text:
                noise_count += 1
                print(f"[NOISE] {doc_id} - (Registry)")
            else:
                print(f"[OTHER] {doc_id} - Potential Noise")
        except:
            print(f"[ERROR] {doc_id}")

    print(f"\nAudit Summary:")
    print(f"Total: {len(rows)}")
    print(f"Real Inheritance Leads: {real_count}")
    print(f"Registry/Noise: {noise_count}")

if __name__ == "__main__":
    audit_boe_n()
