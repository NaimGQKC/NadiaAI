"""Inspect TEJU search result markup and a sample edict document."""
import re
import sys
from datetime import UTC, datetime, timedelta

import requests
from bs4 import BeautifulSoup

sys.stdout.reconfigure(encoding="utf-8")

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"})

end = datetime.now(UTC)
start = end - timedelta(days=10)
params = {
    "campo[0]": "DOC", "dato[0]": '"herencia yacente"', "operador[0]": "and",
    "campo[2]": "JUR", "dato[2]": "", "operador[2]": "and",
    "operador[4]": "and", "campo[4]": "FPU",
    "dato[4][0]": start.strftime("%Y-%m-%d"),
    "dato[4][1]": end.strftime("%Y-%m-%d"),
    "page_hits": "500",
    "sort_field[0]": "FPU", "sort_order[0]": "desc",
    "accion": "Buscar",
}
r = S.get("https://www.boe.es/buscar/edictos_judiciales.php", params=params, timeout=30)
soup = BeautifulSoup(r.text, "html.parser")

# find list items containing BOE-J links
results = []
for li in soup.find_all("li"):
    a = li.find("a", href=re.compile(r"BOE-J-\d+-\d+"))
    if a and li.find("p"):
        results.append(li)
print("result li count:", len(results))
for li in results[:3]:
    text = " ".join(li.get_text(" ", strip=True).split())
    hrefs = [a.get("href") for a in li.find_all("a")]
    print("\nROW TEXT:", text[:300])
    print("HREFS:", hrefs)

# fetch one edict HTML doc
m = re.search(r'href="(/boe_j/dias/[^"]*edi\.php\?id=(BOE-J-\d+-\d+))"', r.text)
if m:
    doc_url = "https://www.boe.es" + m.group(1)
    print("\nDOC URL:", doc_url)
    rd = S.get(doc_url, timeout=20)
    print("status:", rd.status_code, "len:", len(rd.text))
    dsoup = BeautifulSoup(rd.text, "html.parser")
    body = dsoup.find("div", id="textoxslt") or dsoup.find("div", class_="documento-tit") or dsoup
    txt = " ".join(dsoup.get_text(" ", strip=True).split())
    i = txt.lower().find("herencia yacente")
    print("snippet:", txt[max(0, i - 300):i + 300] if i >= 0 else txt[:600])
