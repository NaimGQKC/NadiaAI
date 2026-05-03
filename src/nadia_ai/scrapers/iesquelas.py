"""Scraper for iEsquelas.com — obituary aggregator for Spain.

iEsquelas aggregates obituary/defunción data across Spain. For Zaragoza,
it provides name, date, province, and tanatorio info. This is a third
obituary source complementing Memora and defunciones.es.

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

logger = logging.getLogger("nadia_ai.scrapers.iesquelas")

BASE_URL = "https://iesquelas.com"
ZARAGOZA_URL = BASE_URL + "/zaragoza/"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "NadiaAI/1.0 (real-estate lead pipeline; polite scraper)",
    "Accept": "text/html",
    "Accept-Language": "es-ES,es;q=0.9",
})

# Regex to extract rows from the <table class="esquelas"> tbody
# Each row has 4 cells: name, date (DD-MM-YY), province, tanatorio
# Note: <td> tags may have style attributes (e.g. style=" min-width: 85px;")
ROW_PATTERN = re.compile(
    r"<tr>"
    r"<td[^>]*>(.*?)</td>"
    r"<td[^>]*>(.*?)</td>"
    r"<td[^>]*>(.*?)</td>"
    r"<td[^>]*>(.*?)</td>"
    r"</tr>",
    re.DOTALL,
)

# Only match data rows inside <tbody> (skip <thead>)
TBODY_PATTERN = re.compile(r"<tbody>(.*?)</tbody>", re.DOTALL)

DATE_PATTERN = re.compile(r"(\d{2})-(\d{2})-(\d{2})")


def _parse_date(date_str: str) -> datetime | None:
    """Parse date from DD-MM-YY format."""
    m = DATE_PATTERN.search(date_str.strip())
    if not m:
        return None
    day, month, year_short = int(m.group(1)), int(m.group(2)), int(m.group(3))
    # Two-digit year: 00-49 → 2000-2049, 50-99 → 1950-1999
    year = 2000 + year_short if year_short < 50 else 1900 + year_short
    try:
        return datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        return None


def _clean_name(name_html: str) -> str:
    """Strip HTML tags and normalize whitespace. Convert ALL CAPS to Title Case."""
    text = re.sub(r"<[^>]+>", "", name_html).strip()
    return text.title() if text == text.upper() else text


def _name_to_id(name: str, date_str: str) -> str:
    """Generate a stable source_id from name + date."""
    key = f"iesquelas:{name}:{date_str}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def fetch_page() -> list[dict]:
    """Fetch the Zaragoza obituary listing page. Returns list of raw entry dicts.

    iEsquelas shows a single page with recent entries (~10-20 days).
    No pagination is available.
    """
    try:
        resp = SESSION.get(ZARAGOZA_URL, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("iEsquelas fetch failed: %s", e)
        return []

    # Extract tbody content to skip header row
    tbody_match = TBODY_PATTERN.search(resp.text)
    if not tbody_match:
        logger.warning("iEsquelas: could not find <tbody> in response")
        # Fallback: try matching rows in the full page
        html = resp.text
    else:
        html = tbody_match.group(1)

    entries = []
    for name_html, date_str, province, tanatorio in ROW_PATTERN.findall(html):
        name = _clean_name(name_html)
        if not name:
            continue
        entries.append({
            "name": name,
            "date_str": date_str.strip(),
            "date": _parse_date(date_str),
            "province": province.strip(),
            "tanatorio": tanatorio.strip(),
        })
    return entries


def scrape_iesquelas(since: datetime | None = None) -> list[EdictRecord]:
    """Scrape iEsquelas.com for recent defunciones in Zaragoza province.

    Returns EdictRecord objects suitable for the merge pipeline.
    Default window: 90 days (though iEsquelas typically shows ~10-20 days).
    """
    if since is None:
        since = datetime.now(UTC) - timedelta(days=90)

    entries = fetch_page()
    if not entries:
        logger.info("iEsquelas: no entries found")
        return []

    seen_ids: set[str] = set()
    all_records: list[EdictRecord] = []

    for entry in entries:
        source_id = _name_to_id(entry["name"], entry["date_str"])
        if source_id in seen_ids:
            continue
        seen_ids.add(source_id)

        pub_date = entry["date"]
        if pub_date and pub_date < since:
            continue

        fecha_f = pub_date.strftime("%Y-%m-%d") if pub_date else None

        # Use tanatorio as locality hint if province is generic
        localidad = entry["province"] or "Zaragoza"
        tanatorio = entry["tanatorio"]
        # "A consultar" means unknown tanatorio
        lugar_fallecimiento = tanatorio if tanatorio and tanatorio != "A consultar" else None

        record = EdictRecord(
            source="iesquelas",
            source_id=source_id,
            edict_type="defuncion_esquela",
            published_at=pub_date,
            source_url=ZARAGOZA_URL,
            causante=entry["name"],
            localidad=localidad,
            fecha_fallecimiento=fecha_f,
            lugar_fallecimiento=lugar_fallecimiento,
        )
        all_records.append(record)
        logger.info(
            "iEsquela: %s [%s] (id=%s)",
            entry["name"], localidad, source_id,
        )

    logger.info("iEsquelas scrape complete: %d records", len(all_records))
    return all_records
