import hashlib
import logging
import time
from datetime import UTC, datetime, timedelta

import requests
from bs4 import BeautifulSoup

from nadia_ai.models import EdictRecord
from nadia_ai.utils.text import robust_text
from nadia_ai.utils.names import is_valid_person_name

logger = logging.getLogger("nadia_ai.scrapers.rememori")

from nadia_ai.config import TARGET_PROVINCES

# Canonical host is rememori.com (rememori.es does not resolve).
BASE_URL = "https://www.rememori.com"
PROVINCES = TARGET_PROVINCES
MAX_PAGES_PER_PROVINCE = 3

# Honorific tokens to strip so the name validator (which rejects 2-token
# strings starting with a connector) sees a clean "First Surname Surname".
_HONORIFICS = {"don", "dona", "doña", "d.", "dª", "sr", "sra", "sr.", "sra."}

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 NadiaAI/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
    "Accept-Language": "es-ES,es;q=0.9",
})


def _slug_to_id(name: str, province: str) -> str:
    """Generate stable source_id."""
    return hashlib.md5(f"rememori:{province}:{name}".encode()).hexdigest()[:12]


def _clean_name(raw: str) -> str:
    """Drop a leading honorific (Don/Doña/Sr.) from the obituary name."""
    parts = raw.split()
    while parts and parts[0].lower().strip(".") in {h.strip(".") for h in _HONORIFICS}:
        parts = parts[1:]
    return " ".join(parts).strip()


def fetch_page(province: str, page: int = 1) -> list[dict]:
    """Fetch one page of listings for a province, newest first.

    The listing defaults to alphabetical (slug) order, which buries recent
    deaths behind years of backlog. We force a descending date sort so the
    freshest esquelas appear on page 1.
    """
    sort = "sort:Obituary.created/direction:desc"
    if page == 1:
        url = f"{BASE_URL}/esquelas/{province}/{sort}"
    else:
        url = f"{BASE_URL}/esquelas/{province}/{sort}/page:{page}"

    try:
        resp = SESSION.get(url, timeout=30)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Rememori province %s page %d fetch failed: %s", province, page, e)
        return []

    soup = BeautifulSoup(robust_text(resp), "html.parser")
    table = soup.find("table")
    if not table:
        return []

    rows = table.find_all("tr")
    if len(rows) <= 1:
        return []

    entries = []
    # Skip header row.
    for row in rows[1:]:
        cols = row.find_all("td")
        if len(cols) < 2:
            continue

        date_str = cols[0].get_text(strip=True)
        name_tag = cols[1].find("a")
        if not name_tag:
            continue

        raw_name = name_tag.get_text(strip=True)
        name = _clean_name(raw_name)
        url_path = name_tag.get("href", "")
        locality = cols[2].get_text(strip=True) if len(cols) > 2 else province.title()

        entries.append({
            "name": name,
            "url": BASE_URL + url_path if url_path.startswith("/") else url_path,
            "date_str": date_str,
            "locality": locality,
        })

    return entries


def scrape_rememori(since: datetime | None = None) -> list[EdictRecord]:
    """Scrape Rememori.com obituaries for national coverage, newest first.

    Note on freshness: rememori.com is a largely dormant aggregator — most
    provinces have no obituaries newer than 2022-2024. Because listings are
    now sorted newest-first, this scraper returns whatever is within the
    `since` window and stops early once it walks past the cutoff. On a typical
    recent `since`, that legitimately yields 0 records; it will pick up volume
    automatically if/when the source resumes publishing.
    """
    if since is None:
        since = datetime.now(UTC) - timedelta(days=7)

    all_records: list[EdictRecord] = []
    seen_ids: set[str] = set()

    for province in PROVINCES:
        logger.info("Scraping Rememori for province: %s", province)
        province_records = 0
        stop_province = False

        for page in range(1, MAX_PAGES_PER_PROVINCE + 1):
            entries = fetch_page(province, page)
            if not entries:
                break

            page_had_in_window = False
            for entry in entries:
                published_at = None
                if entry["date_str"]:
                    try:
                        published_at = datetime.strptime(
                            entry["date_str"], "%d/%m/%Y"
                        ).replace(tzinfo=UTC)
                    except ValueError:
                        published_at = None

                # With newest-first sort, a dated entry older than the cutoff
                # means everything after it is older too — stop this province.
                if published_at is not None and published_at < since:
                    stop_province = True
                    continue

                page_had_in_window = True

                name = entry["name"]
                if not is_valid_person_name(name):
                    continue

                source_id = _slug_to_id(name, province)
                if source_id in seen_ids:
                    continue
                seen_ids.add(source_id)

                record = EdictRecord(
                    source="rememori",
                    source_id=source_id,
                    edict_type="defuncion_esquela",
                    published_at=published_at or datetime.now(UTC),
                    source_url=entry["url"],
                    causante=name,
                    localidad=entry["locality"],
                    region=province.title(),
                )
                all_records.append(record)
                province_records += 1

            if stop_province or not page_had_in_window:
                break

            time.sleep(1.0)  # Be polite.

        logger.info("Rememori %s: %d records found", province, province_records)

    logger.info("Rememori scrape complete: %d records total", len(all_records))
    return all_records


def run():
    """Entry point for the scraper runner."""
    return scrape_rememori()
