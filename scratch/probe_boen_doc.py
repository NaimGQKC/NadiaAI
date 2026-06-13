"""Check what the boe_n verification fetch actually returns."""
import re
from datetime import UTC, datetime, timedelta

import requests
from bs4 import BeautifulSoup

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"})

d = datetime.now(UTC) - timedelta(days=1)
date_path = d.strftime("%Y/%m/%d")
url = f"https://www.boe.es/boe_n/dias/{date_path}/index.php?l=N"
html = S.get(url, timeout=30).text
items = re.findall(r'<li class="notif">(.*?)</li>', html, re.DOTALL)

KEYWORDS = [r"\bdeclaraci[oó]n\s+de\s+herederos\b", r"\bherederos\b", r"\babintestato\b"]
target_id = None
target_title = None
for it in items:
    m = re.search(r"<p>(.*?)</p>", it, re.DOTALL)
    if not m:
        continue
    if any(re.search(kw, m.group(1), re.IGNORECASE) for kw in KEYWORDS):
        idm = re.search(r"id=(BOE-[JN]-\d+-\d+)", it)
        if idm:
            target_id = idm.group(1)
            target_title = m.group(1).strip()
            print("item links:", re.findall(r'href="([^"]+)"', it))
            break

print("target:", target_id)
print("title:", target_title)

if target_id:
    for test_url in [
        f"https://www.boe.es/notificaciones/notificacion.php?id={target_id}",
        f"https://www.boe.es/diario_boe/txt.php?id={target_id}",
        f"https://www.boe.es/boe_n/dias/{date_path}/not.php?id={target_id}",
    ]:
        try:
            r = S.get(test_url, timeout=20)
            soup = BeautifulSoup(r.text, "html.parser")
            text = soup.get_text()
            has_kw = bool(re.search(r"hereder", text, re.IGNORECASE))
            print(f"\n{test_url}\n  -> status={r.status_code} len={len(r.text)} kw_in_body={has_kw}")
            if r.status_code == 200:
                snippet = " ".join(text.split())[:400]
                print("  body snippet:", snippet[:400])
        except Exception as e:
            print(f"\n{test_url}\n  -> ERROR {e}")
