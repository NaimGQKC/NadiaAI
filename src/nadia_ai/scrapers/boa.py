"""Scraper for BOA (Boletín Oficial de Aragón) — Junta Distribuidora de Herencias.

Extracts property inventories from intestate succession publications.
BOA publishes irregularly (not daily), so the pipeline is safe to run daily
even when BOA has no new content.
"""

import contextlib
import logging
import re
from datetime import UTC, datetime

import requests
from bs4 import BeautifulSoup

from nadia_ai.models import EdictRecord

logger = logging.getLogger("nadia_ai.scrapers.boa")

# BOA portal URL — will be refined after R2 research lands
BOA_BASE_URL = "https://www.boa.aragon.es"
BOA_SEARCH_URL = f"{BOA_BASE_URL}/cgi-bin/EBOA/BRSCGI"

HEADERS = {
    "User-Agent": "NadiaAI/0.1 (lead-generation research; contact: dev@example.com)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "es-ES,es;q=0.9",
}

# Same cadastral reference patterns as tablon
RC_PATTERN = re.compile(r"\b(\d{7}[A-Z]{2}\d{4}[A-Z]\d{4}[A-Z]{2})\b", re.IGNORECASE)
RC_PATTERN_SHORT = re.compile(r"(?:ref(?:erencia)?\.?\s*catastral)[:\s]+(\S+)", re.IGNORECASE)

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def fetch_boa_search(text_query: str = "herencia abintestato", page: int = 1) -> str:
    """Search the BOA for inheritance-related publications. Returns HTML."""
    params = {
        "CMD": "VERLST",
        "BASE": "BOLE",
        "DOCS": "1-20",
        "SEC": "BUSQUEDA AVANZADA",
        "SEPARESSION": "&&",
        "PUESSION-C": text_query,
    }
    response = SESSION.get(BOA_SEARCH_URL, params=params, timeout=30)
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def fetch_boa_document(url: str) -> str:
    """Fetch a single BOA document page."""
    if not url.startswith("http"):
        url = f"{BOA_BASE_URL}{url}"
    response = SESSION.get(url, timeout=30)
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def extract_ref_catastral(text: str) -> str | None:
    """Extract referencia catastral from BOA text."""
    match = RC_PATTERN.search(text)
    if match:
        return match.group(1).upper()
    match = RC_PATTERN_SHORT.search(text)
    if match:
        return match.group(1).strip().upper()
    return None


def parse_boa_document(html: str, source_url: str = "") -> list[EdictRecord]:
    """Parse a BOA document for inheritance records with ref catastral.

    A single BOA document may contain multiple property references in an
    inventory, so this returns a list.
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator=" ", strip=True)
    records = []

    # BOA documents may list multiple properties in an inventory
    # Find all referencia catastral occurrences
    seen_rcs = set()
    for pattern in [RC_PATTERN, RC_PATTERN_SHORT]:
        for match in pattern.finditer(text):
            raw = match.group(1)
            rc = raw.strip(" .,;:").upper() if pattern == RC_PATTERN_SHORT else raw.upper()
            if rc not in seen_rcs:
                seen_rcs.add(rc)

    if not seen_rcs:
        return records

    # Extract common metadata from the document
    published_at = None
    date_match = re.search(r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})", text)
    if date_match:
        months_es = {
            "enero": 1,
            "febrero": 2,
            "marzo": 3,
            "abril": 4,
            "mayo": 5,
            "junio": 6,
            "julio": 7,
            "agosto": 8,
            "septiembre": 9,
            "octubre": 10,
            "noviembre": 11,
            "diciembre": 12,
        }
        day = int(date_match.group(1))
        month_name = date_match.group(2).lower()
        year = int(date_match.group(3))
        month = months_es.get(month_name)
        if month:
            with contextlib.suppress(ValueError):
                published_at = datetime(year, month, day, tzinfo=UTC)

    # Extract causante
    causante = None
    name_pat = (
        r"(?:causante|fallecid[oa]|difunt[oa]|herencia de)[:\s]+"
        r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,4})"
    )
    causante_match = re.search(
        name_pat,
        text,
        re.IGNORECASE,
    )
    if causante_match:
        causante = causante_match.group(1).strip()

    # Create a record for each property found
    for rc in seen_rcs:
        import hashlib

        source_id = hashlib.md5(f"boa:{source_url}:{rc}".encode()).hexdigest()[:12]
        records.append(
            EdictRecord(
                source="boa",
                source_id=source_id,
                referencia_catastral=rc,
                edict_type="junta_distribuidora_herencias",
                published_at=published_at,
                source_url=source_url,
                causante=causante,
            )
        )

    logger.info("Parsed %d property references from BOA document", len(records))
    return records


def parse_boa_search_results(html: str) -> list[str]:
    """Extract document URLs from BOA search results page."""
    soup = BeautifulSoup(html, "lxml")
    urls = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "EBOA" in href or "BRSCGI" in href:
            urls.append(href)
    return urls


def scrape_boa() -> list[EdictRecord]:
    """Scrape BOA for Junta Distribuidora de Herencias publications.

    Safe to run daily even when BOA has no new content — returns empty list.
    Idempotent: duplicates handled at the database layer via (source, source_id).
    """
    all_records = []

    try:
        search_html = fetch_boa_search()
        doc_urls = parse_boa_search_results(search_html)
        logger.info("BOA search returned %d document URLs", len(doc_urls))

        for url in doc_urls[:20]:  # Cap at 20 documents per run
            try:
                doc_html = fetch_boa_document(url)
                records = parse_boa_document(doc_html, source_url=url)
                all_records.extend(records)
            except requests.RequestException as e:
                logger.error("Failed to fetch BOA document %s: %s", url, e)
                continue

    except requests.RequestException as e:
        logger.error("BOA search failed: %s", e)

    logger.info("BOA scrape complete: %d records total", len(all_records))
    return all_records
