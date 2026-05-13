from nadia_ai.scrapers.boe import fetch_teju_search, parse_teju_results
from datetime import UTC, datetime, timedelta

html = fetch_teju_search('Garcia', since=datetime.now(UTC)-timedelta(days=365))
results = parse_teju_results(html)

print(f"Found {len(results)} results for Garcia")
if results:
    print(f"Sample Title: {results[0]['title'][:200]}")
else:
    with open('boe_final_debug.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print("No results found. Saved boe_final_debug.html for inspection.")
