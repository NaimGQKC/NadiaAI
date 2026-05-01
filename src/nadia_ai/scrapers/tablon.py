"""Scraper for Tablón de Edictos del Ayuntamiento de Zaragoza.

Uses the public JSON API (open data, no auth required) to discover
inheritance-related records — declaración de herederos abintestato,
actas de notoriedad, herencias yacentes — that signal future property listings.

API docs: https://www.zaragoza.es/sede/portal/datos-abiertos/api
Queries both tipo-based and text-based searches for maximum coverage.
"""

import logging
import re
from datetime import UTC, datetime

import requests

from nadia_ai.models import EdictRecord

logger = logging.getLogger("nadia_ai.scrapers.tablon")

# JSON API endpoint — open data, no auth required
TABLON_API_URL = "https://www.zaragoza.es/sede/servicio/tablon-edicto.json"

# Category IDs for herederos declarations
TIPO_IDS = [29, 26]  # 29=Acta notoriedad/Declaracion herederos, 26=Declaraciones de herederos

HEADERS = {
    "User-Agent": "NadiaAI/0.1 (lead-generation research; contact: dev@example.com)",
    "Accept": "application/json",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Patterns to extract deceased name from title field — ordered most-specific first
NAME_PATTERNS = [
    # "fallecimiento de DON/DOÑA NAME" or "defuncion de D. NAME"
    re.compile(
        r"(?:fallecimiento|defunci[oó]n)\s+de\s+(?:(?:DO[NÑ]A?|D\.?a?)\s+)?"
        r"(.+?)(?:\s*[,.]|\s+en\s+|\s+de\s+fecha|\s*$)",
        re.IGNORECASE,
    ),
    # "declaracion de herederos abintestato de DOÑA NAME" / "herederos de D. NAME"
    re.compile(
        r"herederos\s+(?:abintestato\s+)?de\s+(?:(?:DO[NÑ]A?|D\.?a?)\s+)?"
        r"(.+?)(?:\s*[,.]|\s*$)",
        re.IGNORECASE,
    ),
    # "Herencia Yacen(te) de D./Dña. NAME"
    re.compile(
        r"[Hh]erencia\s+[Yy]acen(?:te)?\s+de\s+(?:(?:DO[NÑ]A?|D\.?a?|D[ñÑ]a\.?)\s+)?"
        r"(.+?)(?:\s*[,.]|\s*$)",
        re.IGNORECASE,
    ),
    # "Acta de notoriedad ... DON/DOÑA NAME" (at end of title)
    re.compile(
        r"(?:acta\s+(?:de\s+)?notoriedad).+?(?:DO[NÑ]A?|D\.?a?)\s+"
        r"(.+?)(?:\s*[,.]|\s*$)",
        re.IGNORECASE,
    ),
]

# FIQL text query to catch inheritance edicts that may be miscategorized
TEXT_QUERY = "title==*herederos*,title==*herencia*,title==*abintestato*,title==*notoriedad*"


def fetch_herederos_edicts(since: datetime | None = None, max_rows: int = 200) -> list[dict]:
    """Fetch herederos edicts from the Tablón JSON API.

    Runs multiple queries (by tipo IDs + text search) and deduplicates by record ID
    for maximum coverage.

    Args:
        since: Only return records published after this datetime.
        max_rows: Maximum records per query (API max is 500).

    Returns:
        Deduplicated list of raw API records (dicts).
    """
    seen_ids = set()
    all_results = []

    queries = []

    # Query 1: FIQL OR on tipo IDs
    tipo_fiql = ",".join(f"tipo.id=={t}" for t in TIPO_IDS)
    if since:
        since_str = since.strftime("%Y-%m-%dT%H:%M:%S")
        tipo_fiql = f"({tipo_fiql});publicationDate=ge={since_str}"
    queries.append({"q": tipo_fiql, "rows": min(max_rows, 500), "sort": "publicationDate desc"})

    # Query 2: text-based catch-all (catches miscategorized edicts)
    text_q = TEXT_QUERY
    if since:
        text_q = f"({TEXT_QUERY});publicationDate=ge={since.strftime('%Y-%m-%dT%H:%M:%S')}"
    queries.append({"q": text_q, "rows": min(max_rows, 500), "sort": "publicationDate desc"})

    for params in queries:
        try:
            response = SESSION.get(TABLON_API_URL, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            total = data.get("totalCount", 0)
            results = data.get("result", [])
            logger.info(
                "Tablón query returned %d records (total: %d) [q=%s]",
                len(results),
                total,
                params.get("q", ""),
            )
            for r in results:
                rid = r.get("id")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    all_results.append(r)
        except requests.RequestException as e:
            logger.error("Tablón query failed [q=%s]: %s", params.get("q", ""), e)

    logger.info("Tablón total unique records: %d", len(all_results))
    return all_results


def extract_name_from_title(title: str) -> str | None:
    """Extract the deceased person's name from the edict title.

    Tries multiple patterns to handle varied title formats:
    - "por el fallecimiento de DON GARCIA LOPEZ MARIA"
    - "declaracion herederos abintestato de DOÑA JOAQUINA ORTIZ GARCES"
    - "Herencia Yacen de D. Ramon Jose Chueca Alonso"
    - "Acta notoriedad ... DON BENITO ARTAL ARTAL"
    """
    for pattern in NAME_PATTERNS:
        match = pattern.search(title)
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
