"""Inspect the TEJU search form and try a 'herederos' query."""
import re
import sys

import requests
from bs4 import BeautifulSoup

sys.stdout.reconfigure(encoding="utf-8")

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"})

r = S.get("https://www.boe.es/buscar/edictos_judiciales.php", timeout=15)
soup = BeautifulSoup(r.text, "html.parser")
form = soup.find("form")
print("form action:", form.get("action"), "method:", form.get("method"))
for inp in form.find_all(["input", "select", "textarea"]):
    name = inp.get("name")
    if not name:
        continue
    if inp.name == "select":
        opts = [(o.get("value"), o.get_text(strip=True)) for o in inp.find_all("option")][:12]
        print(f"  select {name}: {opts}")
    else:
        print(f"  input {name} type={inp.get('type')} value={inp.get('value')!r}")

# Try a GET search for 'herederos'
params = {"texto": "herederos", "accion": "Buscar"}
r2 = S.get("https://www.boe.es/buscar/edictos_judiciales.php", params=params, timeout=20)
print("\nsearch GET status:", r2.status_code, "len:", len(r2.text))
m = re.search(r"(\d[\d.,]*)\s*resultados?", r2.text, re.IGNORECASE)
print("results marker:", m.group(0) if m else None)
ids = re.findall(r"BOE-J-\d+-\d+", r2.text)
print("BOE-J ids found:", len(set(ids)), list(set(ids))[:5])
