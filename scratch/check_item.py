import requests
import xml.etree.ElementTree as ET

date = "20260420"
url = f"https://www.boe.es/datosabiertos/api/boe/sumario/{date}"
headers = {"Accept": "application/xml"}
r = requests.get(url, headers=headers)
root = ET.fromstring(r.content)

for item in root.findall(".//item"):
    item_id = item.findtext("identificador")
    if item_id == "BOE-B-2026-12362":
        print(f"ID: {item_id}")
        print(f"Title: {item.findtext('titulo')}")
        print(f"Dept: {item.findtext('departamento')}")
