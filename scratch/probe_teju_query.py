"""Run a real TEJU search query for inheritance terms."""
import re
import sys
from datetime import UTC, datetime, timedelta

import requests

sys.stdout.reconfigure(encoding="utf-8")

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"})

end = datetime.now(UTC)
start = end - timedelta(days=10)

def search(text, jur=""):
    params = {
        "campo[0]": "DOC", "dato[0]": text, "operador[0]": "and",
        "campo[1]": "DEM", "dato[1]": "", "operador[1]": "and",
        "campo[2]": "JUR", "dato[2]": jur, "operador[2]": "and",
        "campo[3]": "NBO", "dato[3]": "",
        "operador[4]": "and", "campo[4]": "FPU",
        "dato[4][0]": start.strftime("%Y-%m-%d"),
        "dato[4][1]": end.strftime("%Y-%m-%d"),
        "page_hits": "500",
        "sort_field[0]": "FPU", "sort_order[0]": "desc",
        "sort_field[1]": "id", "sort_order[1]": "asc",
        "accion": "Buscar",
    }
    r = S.get("https://www.boe.es/buscar/edictos_judiciales.php", params=params, timeout=30)
    ids = sorted(set(re.findall(r"BOE-J-\d+-\d+", r.text)))
    m = re.search(r"([\d.,]+)\s*resultados", r.text, re.IGNORECASE)
    return r, ids, (m.group(1) if m else "?")

for q in ["herederos", '"herencia yacente"', "abintestato", '"declaración de herederos"']:
    r, ids, total = search(q)
    print(f"query={q!r}: status={r.status_code} total={total} ids_on_page={len(ids)}")
    if ids:
        print("  sample:", ids[:5])

# Show what a result row looks like for the first query
r, ids, total = search("herederos")
rows = re.findall(r"<li[^>]*class=\"resultado[^\"]*\"[^>]*>(.*?)</li>", r.text, re.DOTALL)
if not rows:
    # try generic result markup
    chunk = r.text
    i = chunk.find("BOE-J-")
    print("\ncontext around first id:\n", chunk[max(0, i - 600):i + 400])
