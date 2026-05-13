from nadia_ai.scrapers.boe import fetch_teju_search
from datetime import UTC, datetime, timedelta

since = datetime.now(UTC) - timedelta(days=30)
html = fetch_teju_search('"herederos abintestato"', since=since)
with open('boe_debug.html', 'w', encoding='utf-8') as f:
    f.write(html)
print("Saved boe_debug.html")
