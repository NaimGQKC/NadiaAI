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

logger = logging.getLogger("nadia_ai.scrapers.defunciones")

BASE_URL = "https://defunciones.es"
PROVINCE_URL = BASE_URL + "/defunciones/provincia/zaragoza/"
MAX_PAGES = 8

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


def _slug_to_id(slug: str) -> str:
    """Generate stable source_id from slug."""
    return hashlib.md5(f"defunciones:{slug}".encode()).hexdigest()[:12]


def fetch_page(page: int = 1) -> list[dict]:
    """Fetch one page of province listings."""
    url = PROVINCE_URL if page == 1 else f"{PROVINCE_URL}?pag={page}"
    try:
        resp = SESSION.get(url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("defunciones.es page %d fetch failed: %s", page, e)
        return []

    entries = []
    for municipality_slug, name_slug in ENTRY_PATTERN.findall(resp.text):
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
    """Scrape defunciones.es for recent deaths in Zaragoza province.

    Note: defunciones.es doesn't show dates per entry — it's a rolling window
    of ~10-14 days. All visible entries are considered recent.
    The `since` param is accepted for API consistency but not used for filtering.
    """
    if since is None:
        since = datetime.now(UTC) - timedelta(days=90)

    seen_slugs: set[str] = set()
    all_records: list[EdictRecord] = []

    for page in range(1, MAX_PAGES + 1):
        entries = fetch_page(page)
        if not entries:
            logger.info("defunciones.es: no results on page %d, stopping", page)
            break

        new_on_page = 0
        for entry in entries:
            slug = entry["slug"]
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)

            source_id = _slug_to_id(slug)
            record = EdictRecord(
                source="defunciones",
                source_id=source_id,
                edict_type="defuncion_esquela",
                published_at=datetime.now(UTC),  # No exact date; use scrape time
                source_url=entry["url"],
                causante=entry["name"],
                localidad=entry["municipality"],
            )
            all_records.append(record)
            new_on_page += 1
            logger.info(
                "Defunción: %s [%s] (id=%s)",
                entry["name"], entry["municipality"], source_id,
            )

        logger.info("defunciones.es page %d: %d new records", page, new_on_page)

        # Polite delay
        if page < MAX_PAGES:
            time.sleep(1.0)

    logger.info("defunciones.es scrape complete: %d records", len(all_records))
    return all_records
