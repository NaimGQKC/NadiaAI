import requests
import xml.etree.ElementTree as ET
import re
from datetime import datetime, timedelta, UTC
from concurrent.futures import ThreadPoolExecutor

BOE_SUMARIO_URL = "https://www.boe.es/datosabiertos/api/boe/sumario/{date}"
HEADERS = {"Accept": "application/xml"}

def fetch_item_text(doc_id: str) -> str:
    url = f"https://www.boe.es/diario_boe/xml.php?id={doc_id}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            paragraphs = root.findall(".//texto/p")
            return " ".join([p.text for p in paragraphs if p.text])
    except Exception:
        pass
    return ""

def is_inheritance_text(text: str) -> bool:
    keywords = [r"declaraci[oó]n\s+de\s+herederos", r"abintestato", r"sucesi[oó]n", r"herencia"]
    return any(re.search(kw, text, re.IGNORECASE) for kw in keywords)

def diagnostic():
    date_str = (datetime.now(UTC) - timedelta(days=2)).strftime("%Y%m%d")
    print(f"Deep Scanning BOE for {date_str}...")
    r = requests.get(BOE_SUMARIO_URL.format(date=date_str), headers=HEADERS)
    root = ET.fromstring(r.content)
    
    candidates = []
    for seccion in root.findall(".//seccion"):
        sec_code = seccion.get("codigo") or ""
        if sec_code == "4" or sec_code.startswith("5"):
            for dept in seccion.findall(".//departamento"):
                dept_name = (dept.get("nombre") or "").lower()
                for item in dept.findall(".//item"):
                    item_id = item.findtext("identificador", "")
                    candidates.append(item_id)

    print(f"Scanning {len(candidates)} documents...")
    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(lambda id: (id, fetch_item_text(id)), candidates))
    
    matches = 0
    for doc_id, text in results:
        if text and is_inheritance_text(text):
            print(f"[MATCH] {doc_id} | Text Snippet: {text[:100]}...")
            matches += 1

    print(f"\nFound {matches} inheritance leads in Section IV/V today.")

if __name__ == "__main__":
    diagnostic()
