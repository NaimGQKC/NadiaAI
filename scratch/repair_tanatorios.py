import sqlite3
import json
import re
import sys
from pathlib import Path

# Add src to path just in case
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nadia_ai.merge import strip_accents, compute_tier, compute_outreach

def is_funeral_address(addr):
    if not addr:
        return False
    addr_low = strip_accents(str(addr)).lower()
    
    funeral_keywords = [
        "tanatorio", "cementerio", "crematorio", "velatorio", "funeraria", 
        "parcemasa", "camposanto", "tumba", "nicho", "sepultura", 
        "morgue", "tanatori", "cementiri", "crematori", "servisa", 
        "memora", "capilla ardiente", "funeral", "tanatoris", "cementerios"
    ]
    if any(k in addr_low for k in funeral_keywords):
        return True
        
    known_tanatorios = [
        "camino viejo de picassent",
        "colonia de sta. ines",
        "colonia de santa ines",
        "carretera vella de valencia",
        "avenida de castrelos",
        "camino de las torres, 73",
        "camino de las torres 73",
        "placa pau picasso",
        "fray julian garces",
        "fray julian garces",
        "huerta de la fontanilla",
        "miguel romero martinez",
        "loma del esparragal",
        "calle del desconsuelo",
        "carretera n-iv",
        "calle del cementerio",
        "camino del cementerio",
        "junto al cementerio",
        "junto cementerio",
        "calle caleiro, 81",
        "calle caleiro 81",
        "calle caleiro",
        "avenida da ponte, 106",
        "avenida da ponte 106",
        "avenida da ponte",
        "calle canales, 7",
        "calle canales 7",
        "carretera de can ruti",
        "carretera de montcada, 789",
        "carretera de montcada 789",
        "paseo de la sabica",
        "rambla santa ana, 42",
        "rambla santa ana 42",
        "calle aguila",
        "calle aguila",
        "camino de acedinos",
        "camino de san antonio,43",
        "camino de san antonio 43",
        "camino de san antonio, 43",
        "camino de san antonio",
        "camino de los leneros",
        "camino de los leneros",
        "pau redo",
        "los arenales",
        "joan cid i mulet",
        "calle restauracion, 2",
        "calle restauracion 2",
        "pol. ind. matallana"
    ]
    if any(kt in addr_low for kt in known_tanatorios):
        return True
        
    return False

def main():
    conn = sqlite3.connect('nadia_ai.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print("Reading all leads from DB...")
    rows = conn.execute("SELECT * FROM leads").fetchall()
    print(f"Total leads in DB: {len(rows)}")
    
    cleaned_count = 0
    
    for r in rows:
        lead_id = r['id']
        address = r['direccion']
        causante = r['causante']
        
        if address and is_funeral_address(address):
            print(f"Clearing funeral home address on Lead {lead_id} ({causante}):")
            print(f"  Old address: {address}")
            
            # Recalculate lead fields
            lead_dict = dict(r)
            lead_dict['direccion'] = ''
            lead_dict['address_norm'] = ''
            lead_dict['referencia_catastral'] = ''
            lead_dict['ref_catastral'] = ''
            
            # Recalculate tier and outreach
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
    
    print(f"\nDone! Cleaned funeral addresses on {cleaned_count} leads.")

if __name__ == "__main__":
    main()
