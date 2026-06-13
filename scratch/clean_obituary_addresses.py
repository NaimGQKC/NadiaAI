import sqlite3
import json
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nadia_ai.merge import compute_tier, compute_outreach

def main():
    conn = sqlite3.connect('nadia_ai.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print("Scanning leads for obituary-only records with addresses...")
    rows = conn.execute("SELECT * FROM leads WHERE direccion IS NOT NULL AND direccion != ''").fetchall()
    
    cleaned_count = 0
    
    for r in rows:
        lead_id = r['id']
        sources_list = json.loads(r['sources'] or "[]")
        address = r['direccion']
        causante = r['causante']
        
        # Check if lead is obituary-only
        obituary_only = all(s in ("Esquelas", "Defunciones", "iEsquelas", "rememori") for s in sources_list)
        
        if obituary_only:
            print(f"Clearing address on obituary-only Lead {lead_id} ({causante}):")
            print(f"  Old address: {address}")
            
            # Update dict
            lead_dict = dict(r)
            lead_dict['direccion'] = ''
            lead_dict['address_norm'] = ''
            lead_dict['referencia_catastral'] = ''
            lead_dict['ref_catastral'] = ''
            
            # Recalculate tier
            new_tier = compute_tier(lead_dict)
            ok, notes = compute_outreach(lead_dict)
            
            print(f"  New Tier: {new_tier} (was {r['tier']})")
            
            cursor.execute("""
                UPDATE leads
                SET direccion = '',
                    address_norm = '',
                    referencia_catastral = '',
                    ref_catastral = '',
                    tier = ?,
                    outreach_allowed = ?,
                    outreach_notes = ?,
                    last_updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (new_tier, int(ok), notes, lead_id))
            
            cleaned_count += 1
            
    conn.commit()
    conn.close()
    
    print(f"\nDone! Cleared addresses from {cleaned_count} obituary-only leads.")

if __name__ == "__main__":
    main()
