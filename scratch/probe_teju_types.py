"""Enumerate boe_j procedure types and probe TEJU search endpoints."""
import re
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta

import requests

sys.stdout.reconfigure(encoding="utf-8")

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"})

d = datetime.now(UTC) - timedelta(days=1)
date_path = d.strftime("%Y/%m/%d")
url = f"https://www.boe.es/boe_j/dias/{date_path}/index.php?l=J"
html = S.get(url, timeout=30).text
items = re.findall(r'<li class="notif">(.*?)</li>', html, re.DOTALL)

types = Counter()
for it in items:
    m = re.search(r"<p>(.*?)</p>", it, re.DOTALL)
    if not m:
        continue
    t = m.group(1)
    pm = re.search(r"en (?:procedimiento|expediente)\s+(?:de\s+)?(.*?)\s+n[úu]mero", t, re.IGNORECASE)
    if pm:
        types[pm.group(1).lower()[:60]] += 1
    else:
        types["<no-proc-pattern>: " + t[:60].lower()] += 1

print(f"{len(items)} items; procedure types:")
for k, v in types.most_common(40):
    print(f"  {v:5d}  {k}")

# Probe possible TEJU search endpoints
for u in [
    "https://www.boe.es/buscar/edictos.php?campo%5B0%5D=ID_TIPO&dato%5B0%5D=&operador%5B0%5D=and&texto=herederos",
    "https://www.boe.es/buscar/edictos.php",
    "https://www.boe.es/buscar/edictos_judiciales.php",
    "https://www.boe.es/boe_j/buscar.php?texto=herederos",
    "https://www.boe.es/buscar/tej.php",
]:
    try:
        r = S.get(u, timeout=15)
        print(f"\n{u}\n  -> {r.status_code} len={len(r.text)}")
        if r.status_code == 200 and "buscar" in r.text.lower():
            forms = re.findall(r'<form[^>]*action="([^"]*)"', r.text)
            print("  forms:", forms[:5])
    except Exception as e:
        print(f"\n{u}\n  -> ERROR {e}")
