from nadia_ai.scrapers.boe import fetch_teju_search, parse_teju_results
from datetime import UTC, datetime, timedelta
import re

since = datetime.now(UTC) - timedelta(days=30)
html = fetch_teju_search('"herederos abintestato"', since=since)
results = parse_teju_results(html)

print(f"Found {len(results)} results")
for r in results[:10]:
    print("-" * 20)
    print(f"Title: {r['title']}")
