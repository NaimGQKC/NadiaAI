"""Scraper for ITE Zaragoza (Inspección Técnica de Edificios).

Buildings with an unfavorable ITE face mandatory capital expenditures.
These owners are highly likely to liquidate — valuable leads.

Strategy (dual-attempt):
  1. Try Zaragoza Open Data JSON API at candidate endpoints
  2. Fall back to catalog HTML scraping if JSON endpoints 404
  3. Filter for dictamen="desfavorable"
"""

import hashlib
import logging
import re
from datetime import UTC, datetime

import requests
from bs4 import BeautifulSoup

from nadia_ai.models import EdictRecord

logger = logging.getLogger("nadia_ai.scrapers.ite")

API_CANDIDATES = [
    "https://www.zaragoza.es/sede/servicio/urbanismo-infraestructuras/inspeccion-tecnica-edificio.json",
    "https://www.zaragoza.es/sede/servicio/urbanismo-infraestructuras/ite.json",
    "https://www.zaragoza.es/sede/servicio/ite.json",
    "https://www.zaragoza.es/sede/servicio/inspeccion-tecnica-edificio.json",
]

CATALOG_SEARCH_URL = "https://www.zaragoza.es/sede/portal/datos-abiertos/servicio/catalogo"
PAGE_SIZE = 50
MAX_PAGES = 40

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "NadiaAI/1.0 (real-estate lead pipeline; polite scraper)",
    "Accept": "application/json",
})

DESFAVORABLE_KEYWORDS = {"desfavorable", "negativo", "no apto", "deficiente"}


def _make_source_id(address: str, year: str) -> str:
    key = f"ite:{address}:{year}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _is_desfavorable(dictamen: str) -> bool:
    if not dictamen:
        return False
    return any(kw in dictamen.lower().strip() for kw in DESFAVORABLE_KEYWORDS)


def _try_json_api() -> tuple[list[dict], str]:
    """Try each candidate JSON API endpoint. Returns (records, working_url)."""
    for url in API_CANDIDATES:
        try:
            logger.info("ITE: Trying API endpoint: %s", url)
            resp = SESSION.get(url, params={"rows": 5, "start": 0}, timeout=15)
            if resp.status_code == 404:
                continue
            if resp.status_code >= 400:
                continue
            resp.raise_for_status()
            data = resp.json()
            records = data.get("result", []) if isinstance(data, dict) else data
            if records:
                logger.info("ITE: Found working API at %s (%d results)", url, len(records))
                return records, url
        except (requests.RequestException, ValueError) as e:
            logger.debug("ITE: Endpoint %s failed: %s", url, e)
    return [], ""


def _fetch_all_from_api(base_url: str) -> list[dict]:
    """Paginate through a working API endpoint."""
    all_records: list[dict] = []
    start = 0
    while start // PAGE_SIZE < MAX_PAGES:
        try:
            resp = SESSION.get(base_url, params={"rows": PAGE_SIZE, "start": start}, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.error("ITE API fetch failed (start=%d): %s", start, e)
            break
        results = data.get("result", []) if isinstance(data, dict) else data
        if not results:
            break
        all_records.extend(results)
        total = data.get("totalCount", 0) if isinstance(data, dict) else 0
        start += PAGE_SIZE
        if total and start >= total:
            break
    return all_records


def _parse_api_records(records: list[dict]) -> list[EdictRecord]:
    """Parse JSON API records into EdictRecord objects."""
    edicts: list[EdictRecord] = []
    seen_ids: set[str] = set()

    for rec in records:
        if "properties" in rec and isinstance(rec["properties"], dict):
            rec = rec["properties"]

        dictamen = rec.get("dictamen") or rec.get("resultado") or rec.get("calificacion") or ""
        if not _is_desfavorable(dictamen):
            continue

        direccion = rec.get("direccion") or rec.get("address") or rec.get("calle") or ""
        year = str(rec.get("anyo") or rec.get("year") or rec.get("fecha") or "")[:4]
        rc = rec.get("referencia_catastral") or rec.get("clave_catastro") or ""

        if not direccion and not rc:
            continue

        source_id = _make_source_id(direccion[:80] or rc, year or "unknown")
        if source_id in seen_ids:
            continue
        seen_ids.add(source_id)

        pub_date = None
        fecha_raw = rec.get("fecha") or ""
        if isinstance(fecha_raw, str) and len(fecha_raw) >= 10:
            try:
                pub_date = datetime.fromisoformat(fecha_raw[:10]).replace(tzinfo=UTC)
            except (ValueError, TypeError):
                pass

        addr_parts = [direccion] if direccion else []
        addr_parts.append(f"ITE: {dictamen}")
        if year:
            addr_parts.append(f"Año: {year}")

        record = EdictRecord(
            source="ite",
            source_id=source_id,
            edict_type="ite_desfavorable",
            published_at=pub_date,
            source_url="https://www.zaragoza.es/sede/portal/datos-abiertos/",
            referencia_catastral=rc.upper() if rc else None,
            address=" — ".join(addr_parts),
            localidad="Zaragoza",
        )
        edicts.append(record)
        logger.info("ITE desfavorable: %s | %s | %s", (direccion or "-")[:50], dictamen, year or "-")

    return edicts


def _try_catalog_fallback() -> list[EdictRecord]:
    """Search the datos abiertos catalog for ITE-related datasets."""
    session_html = requests.Session()
    session_html.headers.update({
        "User-Agent": "NadiaAI/1.0 (real-estate lead pipeline; polite scraper)",
        "Accept": "text/html",
    })

    for term in ["ITE", "inspeccion tecnica", "edificio"]:
        try:
            resp = session_html.get(CATALOG_SEARCH_URL, params={"q": term, "rows": 20}, timeout=15)
            if resp.status_code != 200:
                continue
            resp.encoding = "utf-8"
        except requests.RequestException:
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        for link in soup.find_all("a", href=True):
            text = link.get_text(strip=True).lower()
            href = link.get("href", "")
            if "ite" in text or "inspecci" in text:
                logger.info("ITE catalog: Found potential link: %s", href)
                if ".json" in href:
                    data_url = href if href.startswith("http") else f"https://www.zaragoza.es{href}"
                    try:
                        data_resp = SESSION.get(data_url, timeout=20)
                        if data_resp.status_code == 200:
                            data = data_resp.json()
                            results = data.get("result", data if isinstance(data, list) else [])
                            if results:
                                return _parse_api_records(results)
                    except (requests.RequestException, ValueError):
                        pass

    logger.warning("ITE: No dataset found in catalog — data may not be publicly available")
    return []


def scrape_ite() -> list[EdictRecord]:
    """Scrape ITE Zaragoza building inspection data.

    Uses dual-attempt: JSON API first, then catalog fallback.
    Returns only unfavorable (desfavorable) inspection results.
    """
    sample_records, working_url = _try_json_api()
    if working_url:
        all_records = _fetch_all_from_api(working_url)
        edicts = _parse_api_records(all_records)
        if edicts:
            logger.info("ITE scrape complete (API): %d desfavorable records", len(edicts))
            return edicts

    logger.info("ITE: All API endpoints failed, trying catalog fallback")
    edicts = _try_catalog_fallback()
    logger.info("ITE scrape complete: %d desfavorable records", len(edicts))
    return edicts
