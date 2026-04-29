"""Scraper for Tablón de Edictos del Ayuntamiento de Zaragoza.

Uses the public JSON API (open data, no auth required) to discover
'declaración de herederos abintestato' records — inheritance events
that signal future property listings.

API docs: https://www.zaragoza.es/sede/portal/datos-abiertos/api
Category filter: tipo.id=29 ("Acta de notoriedad/Declaracion de herederos")
"""

import logging
import re
from datetime import UTC, datetime

import requests

from nadia_ai.models import EdictRecord

logger = logging.getLogger("nadia_ai.scrapers.tablon")

# JSON API endpoint — open data, no auth required
TABLON_API_URL = "https://www.zaragoza.es/sede/servicio/tablon-edicto.json"

# Category ID for herederos declarations
TIPO_HEREDEROS = 29

HEADERS = {
    "User-Agent": "NadiaAI/0.1 (lead-generation research; contact: dev@example.com)",
    "Accept": "application/json",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Pattern to extract deceased name from title field
# Titles look like: "Acta notoriedad para declaracion herederos abintestato,
# por el fallecimiento de DON GARCIA LOPEZ MARIA"
NAME_PATTERN = re.compile(
    r"(?:fallecimiento|defunci[oó]n)\s+de\s+(?:(?:DO[NÑ]A?|D\.?)\s+)?"
    r"(.+?)(?:\s*[,.]|\s+en\s+|\s+de\s+fecha|\s*$)",
    re.IGNORECASE,
)


def fetch_herederos_edicts(since: datetime | None = None, max_rows: int = 50) -> list[dict]:
    """Fetch herederos edicts from the Tablón JSON API.

    Args:
        since: Only return records published after this datetime.
        max_rows: Maximum records to return (API max is 500).

    Returns:
        List of raw API records (dicts).
    """
    params = {
        "tipo.id": TIPO_HEREDEROS,
        "rows": min(max_rows, 500),
        "sort": "publicationDate desc",
    }

    if since:
        params["q"] = f"publicationDate=ge={since.strftime('%Y-%m-%dT%H:%M:%S')}"

    response = SESSION.get(TABLON_API_URL, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    total = data.get("totalCount", 0)
    results = data.get("result", [])
    logger.info("Tablón API returned %d records (total available: %d)", len(results), total)
    return results


def extract_name_from_title(title: str) -> str | None:
    """Extract the deceased person's name from the edict title."""
    match = NAME_PATTERN.search(title)
    if match:
        name = match.group(1).strip()
        # Normalize: titles are often ALL CAPS
        return name.title() if name.isupper() else name
    return None


def parse_api_record(record: dict) -> EdictRecord:
    """Convert a raw API record to an EdictRecord."""
    record_id = str(record.get("id", ""))
    title = record.get("title", "")

    # Parse publication date
    published_at = None
    pub_str = record.get("publicationDate", "")
    if pub_str:
        try:
            published_at = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=UTC)
        except ValueError:
            pass

    # Extract deceased name from title
    causante = extract_name_from_title(title)

    # Build source URL for the PDF document
    source_url = record.get("document", "")
    if not source_url and record_id:
        source_url = f"https://www.zaragoza.es/sede/servicio/tablon-edicto/{record_id}/document"

    return EdictRecord(
        source="tablon",
        source_id=record_id,
        referencia_catastral=None,  # Not available in tablon data
        edict_type="declaracion_herederos_abintestato",
        published_at=published_at,
        source_url=source_url,
        causante=causante,
    )


def scrape_tablon(since: datetime | None = None) -> list[EdictRecord]:
    """Scrape the Tablón de Edictos for inheritance declarations.

    Returns a list of EdictRecord objects for relevant edicts.
    Idempotent: duplicates are handled at the database layer via (source, source_id).
    """
    try:
        raw_records = fetch_herederos_edicts(since=since)
    except requests.RequestException as e:
        logger.error("Failed to fetch Tablón API: %s", e)
        return []

    records = []
    for raw in raw_records:
        record = parse_api_record(raw)
        records.append(record)
        if record.causante:
            logger.info("Tablón lead: %s (source_id=%s)", record.causante, record.source_id)
        else:
            logger.debug("Tablón record %s: could not extract name", record.source_id)

    logger.info("Tablón scrape complete: %d records", len(records))
    return records
