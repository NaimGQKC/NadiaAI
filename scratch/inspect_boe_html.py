import requests
from bs4 import BeautifulSoup

url = "https://www.boe.es/diario_boe/txt.php?id=BOE-B-2026-11878"
r = requests.get(url, timeout=10)
soup = BeautifulSoup(r.text, 'html.parser')

print("=== SELECTORS TEST ===")
selectors = ["#textoxml", "#bloqueTexto", ".documento-texto", ".texto", "main", "#contenido", "#content"]
for sel in selectors:
    el = soup.select_one(sel)
    print(f"Selector '{sel}': {'FOUND' if el else 'NOT FOUND'}")
    if el:
        print(f"  Length: {len(el.get_text())}")
        print(f"  Snippet: {el.get_text()[:100]}...")

print("\n=== DIVS WITH IDS ===")
for div in soup.find_all("div", id=True):
    print(f"Div id: {div['id']}")

print("\n=== DIVS WITH CLASSES ===")
classes = set()
for div in soup.find_all("div", class_=True):
    for c in div["class"]:
        classes.add(c)
print(list(classes))
