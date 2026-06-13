"""Inspect TEJU (boe_j) and Notificaciones (boe_n) HTML item structure."""
import re
from datetime import UTC, datetime, timedelta

import requests

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"})

d = datetime.now(UTC) - timedelta(days=1)
date_path = d.strftime("%Y/%m/%d")

KEYWORDS = [
    r"\bdeclaraci[oó]n\s+de\s+herederos\b", r"\bsucesi[oó]n\b", r"\babintestato\b",
    r"\bherederos\s+de\b", r"\bcausante\b", r"\bherencia\b", r"\bfalleci[óo]n?\b",
    r"\bdefunci[oó]n\b", r"\besquela\b",
]

for folder, label in [("boe_j", "J"), ("boe_n", "N")]:
    url = f"https://www.boe.es/{folder}/dias/{date_path}/index.php?l={label}"
    r = S.get(url, timeout=30)
    html = r.text
    items = re.findall(r'<li class="notif">(.*?)</li>', html, re.DOTALL)
    print(f"\n=== {folder}: {len(items)} li.notif items, html len={len(html)}")
    # show first 3 items raw
    for it in items[:3]:
        print("  RAW:", it[:300].replace("\n", " "))
    # how many have <p> titles and ids
    with_p = sum(1 for it in items if re.search(r"<p>(.*?)</p>", it, re.DOTALL))
    with_id = sum(1 for it in items if re.search(r"id=(BOE-[JN]-\d+-\d+)", it))
    print(f"  items with <p>: {with_p}, with BOE-{label} id: {with_id}")
    # keyword hits on titles
    kw_hits = 0
    sample_match = []
    for it in items:
        m = re.search(r"<p>(.*?)</p>", it, re.DOTALL)
        if not m:
            continue
        title = m.group(1)
        if any(re.search(kw, title, re.IGNORECASE) for kw in KEYWORDS):
            kw_hits += 1
            if len(sample_match) < 3:
                sample_match.append(title[:200])
    print(f"  title keyword hits: {kw_hits}")
    for s in sample_match:
        print("  MATCH:", s.replace("\n", " "))
    # alternative: maybe items use different markup
    alt = re.findall(r'<li class="([^"]*)"', html)
    from collections import Counter
    print("  li classes:", Counter(alt).most_common(8))
