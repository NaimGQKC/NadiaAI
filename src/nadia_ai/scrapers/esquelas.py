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

logger = logging.getLogger("nadia_ai.scrapers.esquelas")

MEMORA_BASE = "https://www.memora.es"
LISTING_URL = MEMORA_BASE + "/esquelas-defunciones-recientes/zaragoza"
MAX_PAGES = 10  # Safety limit — typically 4-6 pages available

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


def fetch_page(page: int = 0) -> list[dict]:
    """Fetch one page of obituary listings. Returns list of raw card dicts."""
    url = LISTING_URL if page == 0 else f"{LISTING_URL}?page={page}"
    try:
        resp = SESSION.get(url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Memora page %d fetch failed: %s", page, e)
        return []

    cards = CARD_PATTERN.findall(resp.text)
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
    """Scrape Memora.es for recent defunciones in Zaragoza province.

    Returns EdictRecord objects suitable for the merge pipeline.
    Default window: 90 days (though Memora typically only shows ~10 days).
    """
    if since is None:
        since = datetime.now(UTC) - timedelta(days=90)

    seen_slugs: set[str] = set()
    all_records: list[EdictRecord] = []

    for page in range(MAX_PAGES):
        cards = fetch_page(page)
        if not cards:
            logger.info("Memora: no results on page %d, stopping", page)
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
                logger.info("Memora: reached date cutoff at page %d", page)
                # Records are sorted newest-first; stop pagination
                return all_records

            source_id = _name_to_slug(slug)
            record = EdictRecord(
                source="esquelas",
                source_id=source_id,
                edict_type="defuncion_esquela",
                published_at=pub_date,
                source_url=card["url"],
                causante=card["name"],
                localidad="Zaragoza",
            )
            all_records.append(record)
            new_on_page += 1
            logger.info("Esquela: %s (id=%s)", card["name"], source_id)

        logger.info("Memora page %d: %d new records", page, new_on_page)

        # Polite delay between pages
        if page < MAX_PAGES - 1:
            time.sleep(1.0)

    logger.info("Esquelas scrape complete: %d records", len(all_records))
    return all_records
