import requests
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

date_str = '20260511'
r = requests.get(f'https://www.boe.es/datosabiertos/api/boe/sumario/{date_str}', headers={'Accept': 'application/xml'})
root = ET.fromstring(r.content)
ids = [item.findtext('identificador') for item in root.findall('.//item') if item.findtext('identificador') and item.findtext('identificador').startswith('BOE-B')]

print(f"Auditing {len(ids)} documents from May 11...")

def check_id(doc_id):
    try:
        r2 = requests.get(f'https://www.boe.es/diario_boe/xml.php?id={doc_id}', timeout=5)
        text = r2.text.lower()
        # Look for the town exclusion too
        if "herencia (ciudad real)" in text or "municipio de herencia" in text:
            return doc_id, False, "town"
        if any(k in text for k in ['abintestato', 'declaracion de herederos', 'herencia yacente', 'bienes relictos']):
            return doc_id, True, "match"
    except:
        pass
    return doc_id, False, "none"

with ThreadPoolExecutor(max_workers=20) as executor:
    results = list(executor.map(check_id, ids))

matches = [doc_id for doc_id, found, reason in results if found]
print(f"Found {len(matches)} real matches: {matches}")
