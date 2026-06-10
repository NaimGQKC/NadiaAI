import hashlib
import logging
import re
import time
from datetime import UTC, datetime, timedelta

import requests
from bs4 import BeautifulSoup

from nadia_ai.models import EdictRecord

logger = logging.getLogger("nadia_ai.scrapers.rememori")

BASE_URL = "https://www.rememori.com"
PROVINCES = [
    "madrid", "barcelona", "valencia", "alicante", "sevilla", 
    "malaga", "murcia", "cadiz", "vizcaya", "coruna", 
    "asturias", "zaragoza", "pontevedra", "granada", "tarragona", 
    "cordoba", "gerona", "almeria", "guipuzcoa", "toledo"
]
MAX_PAGES_PER_PROVINCE = 3

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "NadiaAI/1.0 (real-estate lead pipeline; polite scraper)",
    "Accept": "text/html",
})

def _slug_to_id(name: str, province: str) -> str:
    """Generate stable source_id."""
    return hashlib.md5(f"rememori:{province}:{name}".encode()).hexdigest()[:12]

def fetch_page(province: str, page: int = 1) -> list[dict]:
    """Fetch one page of listings for a province."""
    # Pagination format on rememori.com: /esquelas/[province]/page:[X]
    if page == 1:
        url = f"{BASE_URL}/esquelas/{province}"
    else:
        url = f"{BASE_URL}/esquelas/{province}/page:{page}"
        
    try:
        resp = SESSION.get(url, timeout=30)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Rememori province %s page %d fetch failed: %s", province, page, e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    entries = []
    
    # Table structure found by browser: table with header, then tr/td
    table = soup.find("table")
    if not table:
        return []
        
    rows = table.find_all("tr")
    if len(rows) <= 1:
        return []
        
    # Skip header
    for row in rows[1:]:
        cols = row.find_all("td")
        if len(cols) < 2:
            continue
            
        date_str = cols[0].get_text(strip=True)
        name_tag = cols[1].find("a")
        if not name_tag:
            continue
            
        name = name_tag.get_text(strip=True).title()
        url_path = name_tag["href"]
        locality = cols[2].get_text(strip=True) if len(cols) > 2 else province.title()
        
        entries.append({
            "name": name,
            "url": BASE_URL + url_path if url_path.startswith("/") else url_path,
            "date_str": date_str,
            "locality": locality
        })
            
    return entries

def scrape_rememori(since: datetime | None = None) -> list[EdictRecord]:
    """Scrape Rememori.com for national coverage."""
    if since is None:
        since = datetime.now(UTC) - timedelta(days=7)

    all_records: list[EdictRecord] = []

    for province in PROVINCES:
        logger.info("Scraping Rememori for province: %s", province)
        province_records = 0
        
        for page in range(1, MAX_PAGES_PER_PROVINCE + 1):
            entries = fetch_page(province, page)
            if not entries:
                break

            for entry in entries:
                source_id = _slug_to_id(entry["name"], province)
                
                # Parse date if possible, otherwise use today
                published_at = datetime.now(UTC)
                if entry["date_str"]:
                    try:
                        # Expecting DD/MM/YYYY
                        dt = datetime.strptime(entry["date_str"], "%d/%m/%Y")
                        published_at = dt.replace(tzinfo=UTC)
                    except ValueError:
                        pass
                
                if published_at < since:
                    continue

                record = EdictRecord(
                    source="rememori",
                    source_id=source_id,
                    edict_type="defuncion_esquela",
                    published_at=published_at,
                    source_url=entry["url"],
                    causante=entry["name"],
                    localidad=entry["locality"],
                    region=province.title()
                )
                all_records.append(record)
                province_records += 1

            time.sleep(1.0) # Be polite
            
        logger.info("Rememori %s: %d records found", province, province_records)

    return all_records

def run():
    """Entry point for the scraper runner."""
    return scrape_rememori()
