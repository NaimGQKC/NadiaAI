"""Scraper for BOP Zaragoza (Boletín Oficial de la Provincia de Zaragoza).

Province-wide edict board — covers ALL municipalities in Zaragoza province,
not just the city. This is the single highest-volume source for inheritance
edicts: 50-150+ per year.

Uses HTML scraping of bop.dpz.es (no JSON API available).
"""

import hashlib
import logging
import re
from datetime import UTC, datetime, timedelta

import requests
from bs4 import BeautifulSoup

from nadia_ai.models import EdictRecord

logger = logging.getLogger("nadia_ai.scrapers.bop")

BOP_BASE_URL = "http://bop.dpz.es"
BOP_SEARCH_URL = f"{BOP_BASE_URL}/BOPZ/portalBuscarEdictos.do"

HEADERS = {
    "User-Agent": "NadiaAI/0.1 (lead-generation research; contact: dev@example.com)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "es-ES,es;q=0.9",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Search terms for inheritance edicts
SEARCH_TERMS = [
    "declaracion herederos",
    "herencia abintestato",
    "herencia yacente",
    "acta de notoriedad herederos",
]

# Name extraction patterns
NAME_PATTERNS = [
    # "DECLARACION HEREDEROS FIRST LAST LAST"
    re.compile(
        r"(?:DECLARACION|DECLARACIÓN)\s+(?:DE\s+)?HEREDEROS\s+"
        r"(?:(?:DE|ABINTESTATO\s+DE)\s+)?"
        r"(?:(?:DO[NÑ]A?|D\.?a?)\s+)?"
        r"([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑA-Za-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑA-Za-záéíóúñ][A-Za-záéíóúñ]+){1,5})",
        re.IGNORECASE,
    ),
    # "HERENCIA YACENTE ... DE DON/DOÑA NAME"
    re.compile(
        r"HERENCIA\s+YACENTE\s+.*?(?:DE\s+)?(?:DO[NÑ]A?|D\.?a?)\s+"
        r"([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑa-záéíóúñ][a-záéíóúñA-ZÁÉÍÓÚÑ]+){1,5})",
        re.IGNORECASE,
    ),
    # "fallecimiento de DON/DOÑA NAME"
    re.compile(
        r"(?:fallecimiento|defunci[oó]n)\s+de\s+(?:DO[NÑ]A?|D\.?a?)\s+"
        r"([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑa-záéíóúñ][a-záéíóúñA-ZÁÉÍÓÚÑ]+){1,5})",
        re.IGNORECASE,
    ),
    # "herederos ... de DON/DOÑA NAME"
    re.compile(
        r"herederos\s+.*?de\s+(?:DO[NÑ]A?|D\.?a?)\s+"
        r"([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑa-záéíóúñ][a-záéíóúñA-ZÁÉÍÓÚÑ]+){1,5})",
        re.IGNORECASE,
    ),
]


def extract_name(text: str) -> str | None:
    """Extract deceased person's name from edict title/text."""
    for pattern in NAME_PATTERNS:
        m = pattern.search(text)
        if m:
            name = m.group(1).strip()
            # Clean trailing noise
            name = re.sub(r"\s+(EN|POR|CON|DEL|DE LA|Y SUS|SOBRE)(\s.*)?$", "", name, flags=re.I)
            if name.isupper():
                name = name.title()
            return name
    return None


def search_bop(text_query: str, since: datetime | None = None) -> str:
    """Search BOP for edicts matching text_query. Returns HTML."""
    params = {
        "busqueda": text_query,
    }
    if since:
        params["fechaDesde"] = since.strftime("%d/%m/%Y")
        params["fechaHasta"] = datetime.now(UTC).strftime("%d/%m/%Y")

    response = SESSION.get(BOP_SEARCH_URL, params=params, timeout=60)
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def parse_search_results(html: str, query_label: str = "") -> list[dict]:
    """Parse BOP search results HTML into a list of edict dicts.

    Each dict has: title, url, date, edict_id.
    """
    soup = BeautifulSoup(html, "lxml")
    results = []

    # BOP lists results as links — look for edict links
    for link in soup.find_all("a", href=True):
        href = link["href"]
        text = link.get_text(strip=True)

        # Match edict detail links
        if "obtenerContenidoEdicto" in href or "idEdicto" in href:
            edict_id = None
            id_match = re.search(r"idEdicto=(\d+)", href)
            if id_match:
                edict_id = id_match.group(1)

            date = None
            date_match = re.search(r"fechaPub=(\d{2}/\d{2}/\d{4})", href)
            if date_match:
                try:
                    date = datetime.strptime(date_match.group(1), "%d/%m/%Y").replace(tzinfo=UTC)
                except ValueError:
                    pass

            url = href if href.startswith("http") else f"{BOP_BASE_URL}{href}"

            results.append({
                "title": text,
                "url": url,
                "date": date,
                "edict_id": edict_id or hashlib.md5(href.encode()).hexdigest()[:12],
            })

    # Fallback: parse table rows or list items if links approach found nothing
    if not results:
        for row in soup.find_all(["tr", "li"]):
            text = row.get_text(separator=" ", strip=True)
            if not text or len(text) < 20:
                continue

            # Check for inheritance keywords
            keywords = ["heredero", "herencia", "abintestato", "notoriedad", "fallecimiento"]
            if not any(kw in text.lower() for kw in keywords):
                continue

            # Extract any link in the row
            link = row.find("a", href=True)
            url = ""
            edict_id = hashlib.md5(text[:100].encode()).hexdigest()[:12]
            if link:
                href = link["href"]
                url = href if href.startswith("http") else f"{BOP_BASE_URL}{href}"
                id_match = re.search(r"idEdicto=(\d+)", href)
                if id_match:
                    edict_id = id_match.group(1)

            # Extract date from text
            date = None
            date_match = re.search(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", text)
            if date_match:
                try:
                    date = datetime(
                        int(date_match.group(3)),
                        int(date_match.group(2)),
                        int(date_match.group(1)),
                        tzinfo=UTC,
                    )
                except ValueError:
                    pass

            results.append({
                "title": text[:200],
                "url": url,
                "date": date,
                "edict_id": edict_id,
            })

    return results


def scrape_bop(since: datetime | None = None) -> list[EdictRecord]:
    """Scrape BOP Zaragoza for inheritance-related edicts.

    Runs multiple search terms and deduplicates by edict_id.
    """
    if since is None:
        since = datetime.now(UTC) - timedelta(days=90)

    seen_ids = set()
    all_records = []

    for query in SEARCH_TERMS:
        try:
            html = search_bop(query, since=since)
            results = parse_search_results(html, query_label=query)
            logger.info("BOP query '%s': %d results", query, len(results))

            for result in results:
                eid = result["edict_id"]
                if eid in seen_ids:
                    continue
                seen_ids.add(eid)

                causante = extract_name(result["title"])

                record = EdictRecord(
                    source="bop",
                    source_id=f"bop-{eid}",
                    referencia_catastral=None,
                    edict_type="declaracion_herederos_bop",
                    published_at=result.get("date"),
                    source_url=result.get("url", ""),
                    causante=causante,
                )
                all_records.append(record)

                if causante:
                    logger.info("BOP lead: %s (id=%s)", causante, eid)

        except requests.RequestException as e:
            logger.error("BOP query '%s' failed: %s", query, e)

    logger.info("BOP scrape complete: %d unique records", len(all_records))
    return all_records
