import requests
import xml.etree.ElementTree as ET

url = "https://www.boe.es/datosabiertos/api/boe/sumario/20260420"
headers = {"Accept": "application/xml"}
r = requests.get(url, headers=headers)
root = ET.fromstring(r.content)

target_id = "BOE-B-2026-12362"

for item in root.findall(".//item"):
    if item.findtext("identificador") == target_id:
        print("Found item!")
        # Print siblings or parents? No, ElementTree is hard.
        
# Let's print the whole tree path for this item
def find_path(element, target_id, path=[]):
    if element.findtext("identificador") == target_id:
        return path + [element.tag]
    for child in element:
        res = find_path(child, target_id, path + [element.tag])
        if res: return res
    return None

path = find_path(root, target_id)
print(f"Path: {' -> '.join(path) if path else 'Not found'}")
