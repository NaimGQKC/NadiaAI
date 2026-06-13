from nadia_ai.scrapers.boe import fetch_teju_search
from datetime import UTC, datetime, timedelta
from bs4 import BeautifulSoup
import re

since = datetime.now(UTC) - timedelta(days=365)
html = fetch_teju_search('"declaracion de herederos"', since=since)

soup = BeautifulSoup(html, 'lxml')

# Find items with class "item"
items = soup.find_all(True, class_=re.compile(r'item', re.IGNORECASE))
print(f"Found {len(items)} items with class 'item'")
for i, item in enumerate(items[:5]):
    text = item.get_text(separator=' ', strip=True)
    link = item.find('a', href=True)
    href = link['href'] if link else 'NO LINK'
    print(f"\n--- ITEM {i} ---")
    print(f"Tag: {item.name}, Class: {item.get('class')}")
    print(f"Link: {href}")
    print(f"Text: {text[:200]}")

# Also check <li> tags
print("\n\n=== LI TAGS ===")
lis = soup.find_all('li')
for i, li in enumerate(lis[:10]):
    text = li.get_text(separator=' ', strip=True)
    if len(text) > 30:
        link = li.find('a', href=True)
        href = link['href'] if link else 'NO LINK'
        print(f"\nLI {i}: {text[:200]}")
        print(f"  Link: {href}")
