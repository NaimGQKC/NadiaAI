"""Probe live BOE API: section codes in sumario, supplement URL validity."""
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import requests

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/xml",
})

# 1. Sumario: what section codes exist?
for back in range(1, 4):
    d = datetime.now(UTC) - timedelta(days=back)
    url = f"https://www.boe.es/datosabiertos/api/boe/sumario/{d.strftime('%Y%m%d')}"
    r = S.get(url, timeout=15)
    print(f"\n=== sumario {d.date()} status={r.status_code} len={len(r.text)}")
    if r.status_code != 200:
        continue
    try:
        root = ET.fromstring(r.text.encode("utf-8"))
    except Exception as e:
        print("parse error:", e, r.text[:200])
        continue
    secs = {}
    for sec in root.findall(".//seccion"):
        code = sec.get("codigo")
        name = sec.get("nombre")
        n_items = len(sec.findall(".//item"))
        secs[code] = (name, n_items)
    for code, (name, n) in sorted(secs.items()):
        print(f"  seccion codigo={code!r} nombre={name!r} items={n}")
    # count herederos-ish titles per section
    for sec in root.findall(".//seccion"):
        code = sec.get("codigo")
        hits = 0
        for item in sec.findall(".//item"):
            t = (item.findtext("titulo") or "").lower()
            if re.search(r"hereder|abintestato|sucesi|falleci|defunci|herencia", t):
                hits += 1
        if hits:
            print(f"  -> seccion {code}: {hits} title-level inheritance hits")
    break  # one good day is enough

# 2. Supplement URLs used by scrape_supplemental_leads
d = datetime.now(UTC) - timedelta(days=1)
date_path = d.strftime("%Y/%m/%d")
for folder, label in [("boe_j", "J"), ("boe_n", "N")]:
    url = f"https://www.boe.es/{folder}/dias/{date_path}/index.php?l={label}"
    try:
        r = S.get(url, timeout=15)
        has_notif = '<li class="notif">' in r.text
        print(f"\nsupplement {url} -> status={r.status_code} len={len(r.text)} li.notif={has_notif}")
    except Exception as e:
        print(f"\nsupplement {url} -> ERROR {e}")

# 3. Real TEJU location check
for url in [
    f"https://www.boe.es/boe_n/dias/{date_path}/",
    f"https://www.boe.es/tablon_edictal_judicial_unico/",
    f"https://www.boe.es/diario_boe/calendarios.php",
]:
    try:
        r = S.get(url, timeout=15)
        print(f"check {url} -> {r.status_code} len={len(r.text)}")
    except Exception as e:
        print(f"check {url} -> ERROR {e}")
