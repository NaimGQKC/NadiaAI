import requests
import xml.etree.ElementTree as ET
import re
from datetime import datetime, timedelta, UTC

BOE_SUMARIO_URL = "https://www.boe.es/datosabiertos/api/boe/sumario/{date}"
HEADERS = {"Accept": "application/xml"}

NEGATIVE_KEYWORDS = [
    "animal", "perro", "perra", "canino", "hallazgo", "objeto", "joya", "multa", 
    "tráfico", "vehículo", "sanción", "extranjería", "infracción", 
    "decomiso", "estupefacientes", "costas", "bicicleta", "anillo", "cartera",
    "documentación encontrada", "pérdida", "puntos", "radar"
]

KEYWORDS = [
    r"\bdeclaraci[oó]n\s+de\s+herederos\b",
    r"\bsucesi[oó]n\b",
    r"\babintestato\b",
    r"\btestamentar[ií]a\b",
    r"\bherederos\s+de\b",
    r"\bcausante\b",
    r"\bbienes\s+relictos\b",
    r"\bherencia\s+yacente\b",
    r"\bherencia\b",
    r"\balbacea\b",
    r"\blegatario\b",
    r"\bcontador-partidor\b",
    r"\bfallecimiento\b",
    r"\bdefunci[oó]n\b",
    r"\bnotar[ií]a\b",
]

def diagnostic():
    date_str = (datetime.now(UTC) - timedelta(days=2)).strftime("%Y%m%d")
    url = BOE_SUMARIO_URL.format(date=date_str)
    print(f"Fetching {url}...")
    r = requests.get(url, headers=HEADERS)
    if r.status_code != 200:
        print(f"Error: {r.status_code}")
        return

    root = ET.fromstring(r.content)
    
    total_items = 0
    matches = 0
    
    for seccion in root.findall(".//seccion"):
        sec_code = seccion.get("codigo")
        sec_name = seccion.get("nombre")
        
        is_sec_iv = sec_code == "4"
        is_sec_v = sec_code.startswith("5")
        
        if is_sec_iv or is_sec_v:
            print(f"\n--- Processing Section {sec_code}: {sec_name} ---")
        
        for dept in seccion.findall(".//departamento"):
            dept_name = (dept.get("nombre") or "").lower()
            for item in dept.findall(".//item"):
                total_items += 1
                item_id = item.findtext("identificador", "")
                titulo = (item.findtext("titulo") or "").lower()
                
                text_to_check = f"{titulo} {dept_name}"
                
                # Check for Section IV triggers
                if is_sec_iv:
                    iv_triggers = [r"\bedicto\b", r"\bjuzgado\b", r"\bherencia\b", r"\bcausante\b"]
                    if any(re.search(kw, text_to_check, re.IGNORECASE) for kw in iv_triggers):
                        print(f"[IV MATCH] ID: {item_id} | Title: {titulo[:80]}...")
                        matches += 1
                        continue

                # Check for Section V triggers
                if is_sec_v:
                    if any(re.search(kw, text_to_check, re.IGNORECASE) for kw in KEYWORDS):
                        priority_depts = ["notar", "hacienda", "tribut", "sucesi", "recaudaci", "justicia"]
                        if any(pd in dept_name for pd in priority_depts):
                            print(f"[V MATCH] ID: {item_id} | Title: {titulo[:80]}...")
                            matches += 1
                            continue

    print(f"\nDiagnostic Summary for {date_str}:")
    print(f"Total potential matches: {matches}")

if __name__ == "__main__":
    diagnostic()
