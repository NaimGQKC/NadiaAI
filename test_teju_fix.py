from nadia_ai.scrapers.boe import scrape_teju
from datetime import UTC, datetime, timedelta

since = datetime.now(UTC) - timedelta(days=90)
records = scrape_teju(since=since)
print(f"Total TEJU records found: {len(records)}")

for i, r in enumerate(records[:10]):
    print(f"\n[{i}] {r.causante} | Region: {r.localidad} | Link: {r.source_url}")
