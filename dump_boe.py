from nadia_ai.scrapers.boe import fetch_teju_search
from datetime import UTC, datetime, timedelta
import sys

try:
    since = datetime.now(UTC) - timedelta(days=30)
    html = fetch_teju_search('"herederos"', since=since)
    print(html[:5000])
except Exception as e:
    print(f"Error: {e}")
