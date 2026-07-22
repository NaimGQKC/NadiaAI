"""Scraper for defunciones.es — obituary listings for Zaragoza province.

defunciones.es aggregates funeral home data across Spain. For Zaragoza province,
it provides name + municipality, which gives us better locality data than Memora.
Used as a complementary source alongside the Memora esquelas scraper.
"""

import hashlib
import logging
import re
import time
from datetime import UTC, datetime, timedelta

import requests

from nadia_ai.models import EdictRecord
from nadia_ai.utils.text import robust_text

logger = logging.getLogger("nadia_ai.scrapers.defunciones")

from nadia_ai.config import TARGET_PROVINCES

BASE_URL = "https://defunciones.es"
PROVINCES = TARGET_PROVINCES
MAX_PAGES_PER_PROVINCE = 5

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "NadiaAI/1.0 (real-estate lead pipeline; polite scraper)",
    "Accept": "text/html",
    "Accept-Language": "es-ES,es;q=0.9",
})

# Regex: matches /defunciones/{municipality}/{name-slug}/
ENTRY_PATTERN = re.compile(
    r'href="/defunciones/([a-z][a-z0-9-]+)/([a-z][a-z0-9-]+)/"'
)

# Municipalities to skip (these are category pages, not individual entries)
SKIP_SLUGS = {"provincia"}


def _slug_to_name(slug: str) -> str:
    """Convert URL slug to title-case name."""
    return slug.replace("-", " ").title()


def _slug_to_id(slug: str, province: str) -> str:
    """Generate stable source_id from slug and province."""
    return hashlib.md5(f"defunciones:{province}:{slug}".encode()).hexdigest()[:12]


def fetch_page(province: str, page: int = 1) -> list[dict]:
    """Fetch one page of province listings."""
    province_url = f"{BASE_URL}/defunciones/provincia/{province}/"
    url = province_url if page == 1 else f"{province_url}?pag={page}"
    try:
        resp = SESSION.get(url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("defunciones.es province %s page %d fetch failed: %s", province, page, e)
        return []

    entries = []
    for municipality_slug, name_slug in ENTRY_PATTERN.findall(robust_text(resp)):
        if municipality_slug in SKIP_SLUGS:
            continue
        entries.append({
            "name": _slug_to_name(name_slug),
            "municipality": _slug_to_name(municipality_slug),
            "slug": name_slug,
            "url": f"{BASE_URL}/defunciones/{municipality_slug}/{name_slug}/",
        })
    return entries


def scrape_defunciones(since: datetime | None = None) -> list[EdictRecord]:
    """Scrape defunciones.es for recent deaths across Spain.

    Note: defunciones.es doesn't show dates per entry — it's a rolling window
    of ~10-14 days. All visible entries are considered recent.
    """
    if since is None:
        since = datetime.now(UTC) - timedelta(days=90)

    all_records: list[EdictRecord] = []

    for province in PROVINCES:
        logger.info("Scraping defunciones for province: %s", province)
        seen_slugs: set[str] = set()
        province_records = 0
        
        for page in range(1, MAX_PAGES_PER_PROVINCE + 1):
            entries = fetch_page(province, page)
            if not entries:
                break

            new_on_page = 0
            for entry in entries:
                slug = entry["slug"]
                if slug in seen_slugs:
                    continue
                seen_slugs.add(slug)

                source_id = _slug_to_id(slug, province)
                record = EdictRecord(
                    source="defunciones",
                    source_id=source_id,
                    edict_type="defuncion_esquela",
                    published_at=datetime.now(UTC),
                    source_url=entry["url"],
                    causante=entry["name"],
                    localidad=entry["municipality"],
                    region=province.title()
                )
                all_records.append(record)
                new_on_page += 1
                province_records += 1

            if new_on_page == 0:
                break
            
            time.sleep(0.5)
            
        logger.info("Province %s: %d records found", province, province_records)

    logger.info("defunciones.es total scrape complete: %d records", len(all_records))
    return all_records
