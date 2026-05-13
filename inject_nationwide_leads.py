import sqlite3
from datetime import datetime, UTC
import json

DB_PATH = "nadia_ai.db"

leads = [
  {
    "title": "Anuncio de la Delegación de Economía y Hacienda de Huelva, sobre acuerdo de incoación de procedimiento para declaración administrativa de heredero abintestato a favor de la Administración General del Estado.",
    "url": "https://www.boe.es/buscar/doc.php?id=BOE-B-2026-12330",
    "date": "17/04/2026"
  },
  {
    "title": "Resolución de 9 de abril de 2026 de la Dirección General de Patrimonio de procedimiento para la declaración administrativa de heredero abintestato a favor de la Administración General del Estado.",
    "url": "https://www.boe.es/buscar/doc.php?id=BOE-B-2026-11878",
    "date": "16/04/2026"
  },
  {
    "title": "Anuncio de la Delegación de Economía y Hacienda de Alicante sobre incoación de procedimiento para la declaración administrativa de heredero abintestato a favor de la Administración General del Estado.",
    "url": "https://www.boe.es/buscar/doc.php?id=BOE-B-2026-9218",
    "date": "24/03/2026"
  },
  {
    "title": "Anuncio de la Delegación de Economía y Hacienda de Madrid sobre incoación de expediente para la declaración administrativa de heredero abintestato a favor de la Administración General del Estado.",
    "url": "https://www.boe.es/buscar/doc.php?id=BOE-B-2026-8945",
    "date": "21/03/2026"
  },
  {
    "title": "Anuncio de la Delegación de Economía y Hacienda en Vizcaya de incoación de procedimiento para la declaración administrativa de heredero abintestato a favor de la Administración General del Estado.",
    "url": "https://www.boe.es/buscar/doc.php?id=BOE-B-2026-7531",
    "date": "10/03/2026"
  }
]

def inject_leads():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    count = 0
    for lead in leads:
        region = "Nationwide"
        if "Huelva" in lead["title"]: region = "Huelva"
        if "Alicante" in lead["title"]: region = "Alicante"
        if "Madrid" in lead["title"]: region = "Madrid"
        if "Vizcaya" in lead["title"]: region = "Vizcaya"
        
        causante = "PENDING_LLM_EXTRACT"
        
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO leads (
                    causante, procedimiento, region, sources, source_urls, 
                    kanban_status, first_seen_at, tier
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                causante,
                lead["title"],
                region,
                json.dumps(["BOE"]),
                json.dumps([lead["url"]]),
                "new_to_call",
                datetime.now(UTC).isoformat(),
                "A"
            ))
            if cursor.rowcount > 0:
                count += 1
        except Exception as e:
            print(f"Error injecting {lead['url']}: {e}")
            
    conn.commit()
    conn.close()
    print(f"Successfully injected {count} new nationwide leads!")

if __name__ == "__main__":
    inject_leads()
