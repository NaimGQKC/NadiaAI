"""Scraper for Memora.es — obituary/defunción listings for Zaragoza province.

Memora is Spain's largest funeral services company. Their website publishes
recent defunciones with name, date, and tanatorio info. This provides a
high-volume signal (~100/month for Zaragoza) of recent deaths that can
be cross-referenced with inheritance edicts from BOA/BOE/Tablón.

These are NOT inheritance edicts — they're obituary notices. Leads from
this source are Tier B (name only, no property address) unless enriched
by matching with edict data from other sources.
"""

import hashlib
import logging
import re
import time
from datetime import UTC, datetime, timedelta

import requests

from nadia_ai.models import EdictRecord
from nadia_ai.utils.text import robust_text

logger = logging.getLogger("nadia_ai.scrapers.esquelas")

from nadia_ai.config import TARGET_PROVINCES

MEMORA_BASE = "https://www.memora.es"
CITIES = TARGET_PROVINCES
MAX_PAGES_PER_CITY = 5

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "NadiaAI/1.0 (real-estate lead pipeline; polite scraper)",
    "Accept": "text/html",
    "Accept-Language": "es-ES,es;q=0.9",
})

# Regex to extract obituary cards from Memora HTML
CARD_PATTERN = re.compile(
    r'<a[^>]*href="(/esquelas-defunciones-recientes/[a-z][^"#]+)"[^>]*>\s*'
    r'<div class="name">(.*?)</div>\s*'
    r'<div class="info row">(.*?)</div>',
    re.DOTALL,
)

DATE_PATTERN = re.compile(r"(\d{2})/(\d{2})/(\d{4})")


def _parse_date(info_html: str) -> datetime | None:
    """Extract date from info div (DD/MM/YYYY format)."""
    m = DATE_PATTERN.search(info_html)
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        return None


def _clean_name(name_html: str) -> str:
    """Strip HTML tags and normalize whitespace."""
    text = re.sub(r"<[^>]+>", "", name_html).strip()
    # Memora uses ALL CAPS — convert to title case
    return text.title() if text == text.upper() else text


def _name_to_slug(name: str) -> str:
    """Generate a stable source_id from the Memora URL slug."""
    return hashlib.md5(name.encode("utf-8")).hexdigest()[:12]


def fetch_page(city: str, page: int = 0) -> list[dict]:
    """Fetch one page of obituary listings for a city."""
    listing_url = f"{MEMORA_BASE}/esquelas-defunciones-recientes/{city}"
    url = listing_url if page == 0 else f"{listing_url}?page={page}"
    try:
        resp = SESSION.get(url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Memora city %s page %d fetch failed: %s", city, page, e)
        return []

    cards = CARD_PATTERN.findall(robust_text(resp))
    results = []
    for url_path, name_html, info_html in cards:
        results.append({
            "url": MEMORA_BASE + url_path,
            "name": _clean_name(name_html),
            "date": _parse_date(info_html),
            "slug": url_path.rsplit("/", 1)[-1],
        })
    return results


def scrape_esquelas(since: datetime | None = None) -> list[EdictRecord]:
    """Scrape Memora.es for recent defunciones across major cities.

    Returns EdictRecord objects suitable for the merge pipeline.
    Default window: 90 days (though Memora typically only shows ~10 days).
    """
    if since is None:
        since = datetime.now(UTC) - timedelta(days=90)

    all_records: list[EdictRecord] = []

    for city in CITIES:
        logger.info("Scraping Memora for city: %s", city)
        seen_slugs: set[str] = set()
        city_records = 0
        
        for page in range(MAX_PAGES_PER_CITY):
            cards = fetch_page(city, page)
            if not cards:
                break

            new_on_page = 0
            for card in cards:
                slug = card["slug"]
                if slug in seen_slugs:
                    continue
                seen_slugs.add(slug)

                # Date filter
                pub_date = card["date"]
                if pub_date and pub_date < since:
                    break

                source_id = _name_to_slug(slug)
                fecha_f = pub_date.strftime("%Y-%m-%d") if pub_date else None
                record = EdictRecord(
                    source="esquelas",
                    source_id=source_id,
                    edict_type="defuncion_esquela",
                    published_at=pub_date,
                    source_url=card["url"],
                    causante=card["name"],
                    localidad=city.title(),
                    fecha_fallecimiento=fecha_f,
                )
                all_records.append(record)
                new_on_page += 1
                city_records += 1

            if new_on_page == 0:
                break
                
            time.sleep(0.5)
            
        logger.info("City %s: %d records found", city, city_records)

    logger.info("Memora total scrape complete: %d records", len(all_records))
    return all_records
