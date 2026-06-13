import sqlite3
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nadia_ai.merge import compute_tier, compute_outreach

def main():
    conn = sqlite3.connect('nadia_ai.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 1. Find all duplicate addresses (count > 1)
    duplicates = conn.execute("""
        SELECT direccion, COUNT(*) as c 
        FROM leads 
        WHERE direccion IS NOT NULL AND direccion != ''
        GROUP BY direccion
        HAVING c > 1
    """).fetchall()
    
    print(f"Found {len(duplicates)} duplicate addresses.")
    
    cleared_leads = 0
    
    for dup in duplicates:
        addr = dup['direccion']
        count = dup['c']
        print(f"Clearing address appearing {count} times: '{addr}'")
        
        # Get all leads matching this address
        leads = conn.execute("SELECT * FROM leads WHERE direccion = ?", (addr,)).fetchall()
        for r in leads:
            lead_id = r['id']
            # Update dict
            lead_dict = dict(r)
            lead_dict['direccion'] = ''
            lead_dict['address_norm'] = ''
            lead_dict['referencia_catastral'] = ''
            lead_dict['ref_catastral'] = ''
            
            # Recalculate tier
            new_tier = compute_tier(lead_dict)
            ok, notes = compute_outreach(lead_dict)
            
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
            
            cleared_leads += 1
            
    conn.commit()
    conn.close()
    
    print(f"\nSuccessfully cleared duplicate addresses on {cleared_leads} leads.")

if __name__ == "__main__":
    main()
