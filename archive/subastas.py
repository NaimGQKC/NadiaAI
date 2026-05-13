"""Subastas BOE — Primary lead generator (elevated from enrichment-only).

Promotes the existing enrichment.py subastas parsing logic to a primary
lead source. Instead of only cross-joining auction data to existing leads,
this module pulls ALL upcoming auctions in Zaragoza province and creates
new Tier X (context-only) leads in the database.

Reuses the proven parsing functions from enrichment.py — no code duplication.

Tier X leads have outreach_allowed=false (distress financial signal).
They provide market intelligence even when no direct contact is appropriate.
"""

import hashlib
import logging
import re
from datetime import UTC, datetime

import requests
from bs4 import BeautifulSoup

from nadia_ai.models import EdictRecord

logger = logging.getLogger("nadia_ai.scrapers.subastas")

# Subastas listing pages for Zaragoza province (code 50)
SUBASTAS_LIST_URLS = [
    ("https://subastas.boe.es/subastas_ava.php?provincia=50&tipoSubasta=JV", "JV"),
    ("https://subastas.boe.es/subastas_ava.php?provincia=50&tipoSubasta=NV", "NV"),
]
SUBASTAS_DETAIL_URL = "https://subastas.boe.es/detalleSubasta.php?idSub={id_sub}"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "NadiaAI/1.0 (real-estate lead pipeline; polite scraper)",
    "Accept": "text/html,application/xhtml+xml",
})

SUBASTAS_TIMEOUT = 60  # Their server is slow
MAX_PAGES = 10

# Regex patterns (mirrored from enrichment.py)
_SUBASTA_ID_RE = re.compile(r"idSub=([^&\"']+)")
_RC_RE = re.compile(r"\b(\d{7}[A-Z]{2}\d{4}[A-Z]\d{4}[A-Z]{2})\b", re.IGNORECASE)
_VALOR_RE = re.compile(r"valor\s+(?:de\s+)?tasaci[oó]n[:\s]*([0-9.,]+)", re.IGNORECASE)


def _parse_subastas_list(html: str) -> list[str]:
    """Parse a subastas listing page and return subasta IDs."""
    soup = BeautifulSoup(html, "html.parser")
    ids: list[str] = []
    for link in soup.find_all("a", href=True):
        m = _SUBASTA_ID_RE.search(link["href"])
        if m:
            sub_id = m.group(1)
            if sub_id not in ids:
                ids.append(sub_id)
    return ids


def _get_next_page_url(html: str, base_url: str) -> str | None:
    """Return the URL for the next page of results, or None."""
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.find_all("a", href=True):
        text = link.get_text(strip=True).lower()
        if text in ("siguiente", ">", ">>", "sig"):
            href = link["href"]
            if href.startswith("http"):
                return href
            if href.startswith("/"):
                from urllib.parse import urljoin
                return urljoin(base_url, href)
            return base_url.rsplit("/", 1)[0] + "/" + href
    return None


def _parse_subasta_detail(html: str) -> dict:
    """Parse a subasta detail page and extract key fields."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ", strip=True)

    result: dict = {
        "referencia_catastral": None,
        "direccion": None,
        "valor_tasacion": None,
        "procedimiento": None,
    }

    # Referencia catastral
    rc_match = _RC_RE.search(text)
    if rc_match:
        result["referencia_catastral"] = rc_match.group(1).upper()

    # Valor de tasacion
    valor_match = _VALOR_RE.search(text)
    if valor_match:
        raw = valor_match.group(1).replace(".", "").replace(",", ".")
        try:
            result["valor_tasacion"] = float(raw)
        except ValueError:
            pass

    # Extract address and procedimiento from table rows
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) >= 2:
            label = cells[0].get_text(strip=True).lower()
            value = cells[1].get_text(strip=True)
            if "direcci" in label or "ubicaci" in label or "localizaci" in label:
                if value and len(value) >= 5:
                    result["direccion"] = value
            elif "procedimiento" in label:
                result["procedimiento"] = value

    # Fallback: definition lists
    if not result["direccion"]:
        for dt in soup.find_all("dt"):
            label = dt.get_text(strip=True).lower()
            dd = dt.find_next_sibling("dd")
            if dd and ("direcci" in label or "ubicaci" in label):
                value = dd.get_text(strip=True)
                if value and len(value) >= 5:
                    result["direccion"] = value
            elif dd and "procedimiento" in label:
                result["procedimiento"] = dd.get_text(strip=True)

    return result


def _make_source_id(sub_id: str) -> str:
    """Generate a stable source_id from the subasta ID."""
    return hashlib.md5(f"subasta_lead:{sub_id}".encode()).hexdigest()[:12]


def scrape_subastas_leads() -> list[EdictRecord]:
    """Scrape Subastas BOE as a primary lead generator.

    Fetches all active Zaragoza province auctions (JV + NV) and returns
    them as EdictRecord objects. These will be classified as Tier X
    by the merge engine (distress signal, context-only).

    Returns:
        List of EdictRecord objects with source="subastas".
    """
    all_records: list[EdictRecord] = []
    seen_ids: set[str] = set()

    for base_url, tipo in SUBASTAS_LIST_URLS:
        current_url: str | None = base_url
        page = 0

        while current_url and page < MAX_PAGES:
            page += 1
            try:
                logger.info("Subastas %s page %d: %s", tipo, page, current_url)
                resp = SESSION.get(current_url, timeout=SUBASTAS_TIMEOUT)
                resp.raise_for_status()
                resp.encoding = "utf-8"
            except requests.RequestException as e:
                logger.error("Subastas %s page %d failed: %s", tipo, page, e)
                break

            sub_ids = _parse_subastas_list(resp.text)
            logger.info("Subastas %s page %d: %d results", tipo, page, len(sub_ids))

            if not sub_ids:
                break

            for sub_id in sub_ids:
                if sub_id in seen_ids:
                    continue
                seen_ids.add(sub_id)

                detail_url = SUBASTAS_DETAIL_URL.format(id_sub=sub_id)
                try:
                    detail_resp = SESSION.get(detail_url, timeout=SUBASTAS_TIMEOUT)
                    detail_resp.raise_for_status()
                    detail_resp.encoding = "utf-8"
                except requests.RequestException as e:
                    logger.warning("Subasta detail %s failed: %s", sub_id, e)
                    continue

                detail = _parse_subasta_detail(detail_resp.text)

                # Build composite address with context
                addr_parts = []
                if detail.get("direccion"):
                    addr_parts.append(detail["direccion"])
                if detail.get("valor_tasacion"):
                    addr_parts.append(f"Tasación: €{detail['valor_tasacion']:,.0f}")
                if detail.get("procedimiento"):
                    addr_parts.append(detail["procedimiento"])
                composite = " — ".join(addr_parts) if addr_parts else None

                edict_type = "subasta_judicial" if tipo == "JV" else "subasta_notarial"
                source_id = _make_source_id(sub_id)

                record = EdictRecord(
                    source="subastas",
                    source_id=source_id,
                    edict_type=edict_type,
                    source_url=detail_url,
                    referencia_catastral=detail.get("referencia_catastral"),
                    address=composite,
                    localidad="Zaragoza",
                )
                all_records.append(record)
                logger.info(
                    "Subasta lead: %s (%s) — RC=%s addr=%s",
                    sub_id, tipo,
                    detail.get("referencia_catastral") or "-",
                    (detail.get("direccion") or "-")[:60],
                )

            current_url = _get_next_page_url(resp.text, current_url)

    logger.info("Subastas lead gen complete: %d records", len(all_records))
    return all_records
