import requests
import xml.etree.ElementTree as ET

url = "https://www.boe.es/datosabiertos/api/boe/sumario/20260420"
headers = {"Accept": "application/xml"}
r = requests.get(url, headers=headers)
root = ET.fromstring(r.content)

target_id = "BOE-B-2026-12362"

for dept in root.findall(".//departamento"):
    for item in dept.findall(".//item"):
        if item.findtext("identificador") == target_id:
            print(f"ID: {target_id}")
            print(f"Title: {item.findtext('titulo')}")
            print(f"Departamento: {dept.get('nombre')}")
            # Also check epigrafe if it exists
            parent_epi = root.find(f".//epigrafe[item/identificador='{target_id}']")
            if parent_epi is not None:
                print(f"Epigrafe: {parent_epi.get('nombre')}")
