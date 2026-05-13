from nadia_ai.scrapers.boe import fetch_teju_search
from datetime import UTC, datetime, timedelta

# Search for a super common name to guarantee results
html = fetch_teju_search('Garcia', since=datetime.now(UTC)-timedelta(days=365))
with open('boe_results_test.html', 'w', encoding='utf-8') as f:
    f.write(html)
print("Saved boe_results_test.html")
