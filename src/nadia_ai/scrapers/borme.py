"""Scraper for BORME (Boletín Oficial del Registro Mercantil).

Discovers B2B leads from death-related corporate events (cese por defuncion,
disolucion, apertura de liquidacion, extincion) involving Zaragoza-province
companies. Uses the same BOE Open Data XML API infrastructure as the BOE
scraper.

Two sub-sources:
1. Seccion I  — filter <emisor> containing "Zaragoza" for relevant keywords.
2. Seccion II — regex full body text for Zaragoza-province domicilios + keywords.

Special heuristic: companies whose name contains real-estate keywords
(INMUEBLES, INMOBILIARIA, PATRIMONIAL, etc.) are flagged in outreach_notes
for prioritised follow-up.

Volume: 3-8 entries/month. Zero on most days is normal (business gazette,
no weekends/holidays, low hit rate for death-related events).
"""

import hashlib
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from nadia_ai.models import EdictRecord

logger = logging.getLogger("nadia_ai.scrapers.borme")

# --- API endpoints ---
BORME_SUMARIO_URL = "https://boe.es/datosabiertos/api/borme/sumario/{date}"
BORME_DOC_XML_URL = "https://www.boe.es/diario_boe/xml.php?id={doc_id}"

HEADERS = {
    "User-Agent": "NadiaAI-Pipeline/2.0",
    "Accept": "application/xml",
    "Accept-Language": "es-ES,es;q=0.9",
}

# Session with automatic retries (3 retries, exponential backoff)
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
_retry = Retry(
    total=3,
    backoff_factor=1.0,  # 1s, 2s, 4s exponential backoff
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
)
SESSION.mount("https://", HTTPAdapter(max_retries=_retry))
SESSION.mount("http://", HTTPAdapter(max_retries=_retry))

# --- Keyword patterns ---

# Death/dissolution keywords for filtering BORME items
DEATH_KEYWORDS_RE = re.compile(
    r"cese.*defunci[óo]n|disoluci[óo]n|apertura.*liquidaci[óo]n|extinci[óo]n",
    re.IGNORECASE,
)

# Inmobiliaria heuristic: company names suggesting real-estate activity
INMOBILIARIA_RE = re.compile(
    r"INMUEBLES|INMOBILIARIA|PATRIMONIAL|PROPIEDADES|FINCAS|EDIFICIOS|REAL\s+ESTATE",
    re.IGNORECASE,
)

# NIF pattern (Spanish company tax ID): letter + 8 digits or 7 digits + letter
NIF_RE = re.compile(r"\b([A-Z]\d{7,8}[A-Z0-9]?)\b")

# Zaragoza province detection in domicilio text (Seccion II body scanning)
ZARAGOZA_DOMICILIO_RE = re.compile(
    r"(?:Zaragoza|ZARAGOZA|50[0-9]{3})",  # city name or postal code 50xxx
    re.IGNORECASE,
)

# Address extraction — "Domicilio:" or "domicilio social:" followed by address text
DOMICILIO_EXTRACT_RE = re.compile(
    r"[Dd]omicilio(?:\s+social)?[:\s]+([^\n.;]{10,120})",
)

# Rate limiting: minimum interval between requests (seconds)
_MIN_REQUEST_INTERVAL = 1.0
_last_request_time = 0.0


def _rate_limit() -> None:
    """Enforce max 1 request/sec to respect BOE API limits."""
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if elapsed < _MIN_REQUEST_INTERVAL:
        time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.monotonic()


# ── Summary fetching ───────────────────────────────────────────────


def fetch_borme_sumario(date: datetime) -> str | None:
    """Fetch the BORME daily summary XML for a given date.

    Returns the XML string, or None if no BORME was published that day
    (weekends, holidays → 404 is expected and handled gracefully).
    """
    url = BORME_SUMARIO_URL.format(date=date.strftime("%Y%m%d"))
    _rate_limit()
    try:
        response = SESSION.get(url, timeout=15)
        if response.status_code == 404:
            logger.debug("No BORME for %s (404 — weekend/holiday)", date.strftime("%Y-%m-%d"))
            return None
        response.raise_for_status()
        response.encoding = "utf-8"
        return response.text
    except requests.RequestException as e:
        if "404" in str(e):
            logger.debug("No BORME for %s (404)", date.strftime("%Y-%m-%d"))
            return None
        logger.warning("Failed to fetch BORME sumario %s: %s", date.strftime("%Y-%m-%d"), e)
        return None


def fetch_borme_doc_xml(doc_id: str) -> str | None:
    """Fetch a single BORME document XML by its ID (e.g., BORME-A-2026-1234).

    Returns the XML string, or None on failure.
    """
    url = BORME_DOC_XML_URL.format(doc_id=doc_id)
    _rate_limit()
    try:
        response = SESSION.get(url, timeout=15)
        if response.status_code == 404:
            logger.debug("BORME doc not found: %s", doc_id)
            return None
        response.raise_for_status()
        response.encoding = "utf-8"
        return response.text
    except requests.RequestException as e:
        logger.error("Failed to fetch BORME doc %s: %s", doc_id, e)
        return None


# ── Summary parsing ────────────────────────────────────────────────


def _classify_edict_type(text: str) -> str:
    """Classify edict type based on keywords found in text.

    Returns 'cese_defuncion_borme' for death-related events,
    'disolucion_borme' for dissolution/liquidation/extinction events.
    """
    lower = text.lower()
    if re.search(r"cese.*defunci[óo]n", lower):
        return "cese_defuncion_borme"
    # dissolution, liquidation, extinction are all dissolution-class
    return "disolucion_borme"


def extract_seccion1_items(sumario_xml: str) -> list[dict]:
    """Extract matching items from BORME Seccion I.

    Seccion I items have <emisor> tags. We filter for those containing
    "Zaragoza" and whose title/text matches death/dissolution keywords.

    Returns list of dicts with keys: doc_id, titulo, emisor, seccion.
    """
    items = []
    try:
        root = ET.fromstring(sumario_xml)
    except ET.ParseError as e:
        logger.error("Failed to parse BORME sumario XML: %s", e)
        return items

    for item in root.iter("item"):
        doc_id = item.get("id", "")
        if not doc_id:
            continue

        emisor_el = item.find("emisor")
        emisor = (emisor_el.text or "").strip() if emisor_el is not None else ""

        titulo_el = item.find("titulo")
        titulo = (titulo_el.text or "").strip() if titulo_el is not None else ""

        # Seccion I filter: emisor must reference Zaragoza
        if "zaragoza" not in emisor.lower():
            continue

        # Must match at least one death/dissolution keyword
        combined = f"{titulo} {emisor}"
        if not DEATH_KEYWORDS_RE.search(combined):
            continue

        items.append({
            "doc_id": doc_id,
            "titulo": titulo,
            "emisor": emisor,
            "seccion": "I",
        })

    return items


def extract_seccion2_items(sumario_xml: str) -> list[dict]:
    """Extract matching items from BORME Seccion II.

    Seccion II items are scanned for Zaragoza-province domicilios in
    their full text/title, combined with death/dissolution keywords.

    Returns list of dicts with keys: doc_id, titulo, emisor, seccion.
    """
    items = []
    try:
        root = ET.fromstring(sumario_xml)
    except ET.ParseError:
        return items

    for item in root.iter("item"):
        doc_id = item.get("id", "")
        if not doc_id:
            continue

        # Skip items already captured in Seccion I (typically BOE-A prefixed)
        # Seccion II items are typically BOE-B or BORME-B prefixed
        titulo_el = item.find("titulo")
        titulo = (titulo_el.text or "").strip() if titulo_el is not None else ""

        emisor_el = item.find("emisor")
        emisor = (emisor_el.text or "").strip() if emisor_el is not None else ""

        # For Seccion II, check the full text blob for Zaragoza references
        combined = f"{titulo} {emisor}"

        # Must mention Zaragoza somehow (name or postal code)
        if not ZARAGOZA_DOMICILIO_RE.search(combined):
            continue

        # Must match death/dissolution keywords
        if not DEATH_KEYWORDS_RE.search(combined):
            continue

        items.append({
            "doc_id": doc_id,
            "titulo": titulo,
            "emisor": emisor,
            "seccion": "II",
        })

    return items


# ── Document parsing ───────────────────────────────────────────────


def parse_borme_document(xml_text: str, doc_id: str, seccion: str) -> EdictRecord | None:
    """Parse a BORME document XML into an EdictRecord.

    Extracts company name (→ causante), domicilio (→ address), NIF,
    matched keywords, and applies the inmobiliaria heuristic.

    Args:
        xml_text: The raw XML of the BORME document.
        doc_id: The document identifier (e.g., BORME-A-2026-1234).
        seccion: "I" or "II" — determines the subsource code.

    Returns:
        An EdictRecord if parsing succeeds, None otherwise.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.error("Failed to parse BORME doc XML: %s", doc_id)
        return None

    # Extract metadata fields
    title = ""
    pub_date = None
    pdf_url = ""

    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag == "titulo" and not title:
            title = (el.text or "").strip()
        elif tag == "fecha_publicacion":
            try:
                pub_date = datetime.strptime((el.text or "").strip(), "%Y%m%d").replace(tzinfo=UTC)
            except ValueError:
                pass
        elif tag == "url_pdf":
            pdf_url = (el.text or "").strip()

    # Extract full text body
    body_text = ""
    texto_el = root.find(".//texto")
    if texto_el is not None:
        body_text = ET.tostring(texto_el, encoding="unicode", method="text")

    combined = f"{title} {body_text}"

    # Company name: use the title as causante (these are B2B leads)
    # BORME titles typically ARE the company name or contain it prominently
    causante = title if title else None

    # Classify edict type
    edict_type = _classify_edict_type(combined)

    # Extract domicilio/address
    address = None
    dom_match = DOMICILIO_EXTRACT_RE.search(combined)
    if dom_match:
        address = dom_match.group(1).strip().rstrip(",.")

    # Extract NIF
    nif = None
    nif_match = NIF_RE.search(combined)
    if nif_match:
        nif = nif_match.group(1)

    # Build source URL
    source_url = pdf_url
    if not source_url:
        source_url = f"https://www.boe.es/diario_boe/txt.php?id={doc_id}"

    # Inmobiliaria heuristic
    outreach_notes = ""
    if causante and INMOBILIARIA_RE.search(causante):
        outreach_notes = "INMOBILIARIA — empresa del sector inmobiliario"
        logger.info("BORME inmobiliaria lead flagged: %s", causante)

    # Subsource code
    subsource = "borme_i" if seccion == "I" else "borme_ii"

    # Build localidad from address if Zaragoza-related
    localidad = None
    if address and ZARAGOZA_DOMICILIO_RE.search(address):
        localidad = "Zaragoza"

    # Compose system notes with NIF and outreach flag
    notas_parts = []
    if nif:
        notas_parts.append(f"NIF: {nif}")
    if outreach_notes:
        notas_parts.append(outreach_notes)

    record = EdictRecord(
        source=subsource,
        source_id=doc_id,
        referencia_catastral=None,  # BORME does not contain catastral references
        edict_type=edict_type,
        published_at=pub_date,
        source_url=source_url,
        causante=causante,
        address=address,
        localidad=localidad,
    )

    # Store extra metadata in petitioners field as a workaround for notes
    # (EdictRecord has no outreach_notes field; petitioners is repurposed
    # to carry NIF + inmobiliaria flag for downstream pipeline stages)
    if notas_parts:
        record.petitioners = notas_parts

    return record


# ── Main entry point ───────────────────────────────────────────────


def scrape_borme(since: datetime | None = None) -> list[EdictRecord]:
    """Scrape BORME for death/dissolution events involving Zaragoza companies.

    Fetches daily summaries from the BOE Open Data API for the specified
    date range, identifies Seccion I and II items matching Zaragoza +
    death/dissolution keywords, then fetches and parses each matching
    document.

    Args:
        since: Start date for the scrape window. Defaults to 7 days ago
               (BORME is low-volume; wider window catches holiday gaps).

    Returns:
        Deduplicated list of EdictRecord objects. Empty list on days with
        no BORME or no matching items — this is expected and normal.
    """
    if since is None:
        since = datetime.now(UTC) - timedelta(days=7)

    seen_ids: set[str] = set()
    records: list[EdictRecord] = []

    current = since
    end = datetime.now(UTC)

    while current <= end:
        date_str = current.strftime("%Y-%m-%d")

        sumario_xml = fetch_borme_sumario(current)
        if sumario_xml is None:
            current += timedelta(days=1)
            continue

        # Extract matching items from both sections
        sec1_items = extract_seccion1_items(sumario_xml)
        sec2_items = extract_seccion2_items(sumario_xml)
        all_items = sec1_items + sec2_items

        if all_items:
            logger.info(
                "BORME %s: %d matching items (Sec.I=%d, Sec.II=%d)",
                date_str, len(all_items), len(sec1_items), len(sec2_items),
            )

        for item in all_items:
            doc_id = item["doc_id"]

            # Deduplicate within the scrape
            if doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)

            # Fetch and parse the full document
            doc_xml = fetch_borme_doc_xml(doc_id)
            if doc_xml is None:
                continue

            record = parse_borme_document(doc_xml, doc_id, item["seccion"])
            if record:
                records.append(record)
                logger.info(
                    "BORME lead: %s [%s] (id=%s, type=%s)",
                    record.causante or "?",
                    record.source,
                    doc_id,
                    record.edict_type,
                )

        current += timedelta(days=1)

    logger.info("BORME scrape complete: %d records", len(records))
    return records
