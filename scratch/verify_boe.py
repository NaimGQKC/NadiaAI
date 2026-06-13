import requests
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

date_str = '20260512'
r = requests.get(f'https://www.boe.es/datosabiertos/api/boe/sumario/{date_str}', headers={'Accept': 'application/xml'})
root = ET.fromstring(r.content)
ids = [item.findtext('identificador') for item in root.findall('.//item') if item.findtext('identificador') and item.findtext('identificador').startswith('BOE-B')]

print(f"Scanning {len(ids)} documents from May 12...")

def check_id(doc_id):
    try:
        r2 = requests.get(f'https://www.boe.es/diario_boe/xml.php?id={doc_id}', timeout=5)
        text = r2.text.lower()
        if any(k in text for k in ['herencia', 'herederos', 'abintestato', 'causante']):
            return doc_id, True
    except:
        pass
    return doc_id, False

with ThreadPoolExecutor(max_workers=20) as executor:
    results = list(executor.map(check_id, ids))

matches = [doc_id for doc_id, found in results if found]
print(f"Found {len(matches)} matches: {matches}")
