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


def _item_field(item: ET.Element, name: str) -> str:
    """Read a sumario <item> child field.

    The BOE Open Data API moved the document id from an `id` attribute on
    <item> to a child <identificador> element, and dropped the <emisor>
    child entirely. This helper reads child text, falling back to the legacy
    attribute for `id`.
    """
    el = item.find(name)
    if el is not None and el.text:
        return el.text.strip()
    return ""


def _item_doc_id(item: ET.Element) -> str:
    """Resolve a sumario item's document id across old and new schemas."""
    ident = _item_field(item, "identificador")
    if ident:
        return ident
    # Legacy schema: id was an attribute on <item>
    return item.get("id", "")


def extract_seccion1_items(sumario_xml: str) -> list[dict]:
    """Extract Zaragoza items from BORME Sección A (Empresarios. Actos inscritos).

    In the current BOE Open Data schema the daily summary groups Sección A by
    province: each <item> has a <titulo> equal to a province name (e.g.
    "ZARAGOZA") and an <identificador> like BORME-A-2026-111-50. There is no
    per-company emisor and no keyword text at the summary level — the company
    acts live inside the per-province document. So here we simply locate the
    Zaragoza province document(s); keyword filtering happens after we fetch and
    split the document body in parse_borme_document().

    Returns list of dicts with keys: doc_id, titulo, emisor, seccion.
    """
    items = []
    try:
        root = ET.fromstring(sumario_xml)
    except ET.ParseError as e:
        logger.error("Failed to parse BORME sumario XML: %s", e)
        return items

    for seccion in root.iter("seccion"):
        if (seccion.get("codigo") or "").upper() != "A":
            continue
        for item in seccion.iter("item"):
            doc_id = _item_doc_id(item)
            if not doc_id:
                continue
            titulo = _item_field(item, "titulo")
            # Sección A item title is the province name.
            if "zaragoza" not in titulo.lower():
                continue
            items.append({
                "doc_id": doc_id,
                "titulo": titulo,
                "emisor": titulo,
                "seccion": "I",
            })

    return items


def extract_seccion2_items(sumario_xml: str) -> list[dict]:
    """Extract Zaragoza items from BORME Sección B (Otros actos publicados).

    Sección B items carry a descriptive <titulo> (the act + company), so we can
    filter at the summary level on a Zaragoza reference plus a death/dissolution
    keyword. Many BORME days have no Sección B at all — that is normal.

    Returns list of dicts with keys: doc_id, titulo, emisor, seccion.
    """
    items = []
    try:
        root = ET.fromstring(sumario_xml)
    except ET.ParseError:
        return items

    for seccion in root.iter("seccion"):
        if (seccion.get("codigo") or "").upper() != "B":
            continue
        for item in seccion.iter("item"):
            doc_id = _item_doc_id(item)
            if not doc_id:
                continue
            titulo = _item_field(item, "titulo")

            # Must mention Zaragoza (name or postal code) and a death/dissolution
            # keyword in the summary title.
            if not ZARAGOZA_DOMICILIO_RE.search(titulo):
                continue
            if not DEATH_KEYWORDS_RE.search(titulo):
                continue

            items.append({
                "doc_id": doc_id,
                "titulo": titulo,
                "emisor": titulo,
                "seccion": "II",
            })

    return items


# ── Document parsing ───────────────────────────────────────────────


# A Sección A company block opens with "<registro> - <COMPANY NAME>." Used to
# split the per-province document body into individual company entries.
_COMPANY_HEADER_RE = re.compile(r"^\s*(\d{3,7})\s*-\s*(.+?)\.?\s*$")


def _build_borme_record(
    *,
    company: str,
    act_text: str,
    doc_id: str,
    seccion: str,
    pub_date: datetime | None,
    pdf_url: str,
    suffix: str = "",
) -> EdictRecord:
    """Construct a single EdictRecord for one BORME company act."""
    combined = f"{company} {act_text}"

    edict_type = _classify_edict_type(combined)

    address = None
    dom_match = DOMICILIO_EXTRACT_RE.search(act_text)
    if dom_match:
        address = dom_match.group(1).strip().rstrip(",.")

    nif = None
    nif_match = NIF_RE.search(combined)
    if nif_match:
        nif = nif_match.group(1)

    source_url = pdf_url or f"https://www.boe.es/diario_borme/txt.php?id={doc_id}"

    outreach_notes = ""
    if company and INMOBILIARIA_RE.search(company):
        outreach_notes = "INMOBILIARIA — empresa del sector inmobiliario"
        logger.info("BORME inmobiliaria lead flagged: %s", company)

    subsource = "borme_i" if seccion == "I" else "borme_ii"

    localidad = None
    if (address and ZARAGOZA_DOMICILIO_RE.search(address)) or seccion == "I":
        localidad = "Zaragoza"

    notas_parts = []
    if nif:
        notas_parts.append(f"NIF: {nif}")
    if outreach_notes:
        notas_parts.append(outreach_notes)

    # Make source_id unique per company within a province document.
    source_id = f"{doc_id}{suffix}"

    record = EdictRecord(
        source=subsource,
        source_id=source_id,
        referencia_catastral=None,  # BORME does not contain catastral references
        edict_type=edict_type,
        published_at=pub_date,
        source_url=source_url,
        causante=company or None,
        address=address,
        localidad=localidad,
    )

    if notas_parts:
        record.petitioners = notas_parts

    return record


def parse_borme_document(xml_text: str, doc_id: str, seccion: str) -> list[EdictRecord]:
    """Parse a BORME document XML into a list of EdictRecords.

    Sección A documents are per-province bundles: the <texto> body is a flat
    list of <p> elements alternating between a company header
    ("<registro> - <COMPANY NAME>.") and that company's registered acts. We pair
    each header with its act paragraph, keep only the pairs whose act text
    matches a death/dissolution keyword, and emit one record per company.

    Sección B documents describe a single act; we emit one record built from the
    whole body.

    Args:
        xml_text: The raw XML of the BORME document.
        doc_id: The document identifier (e.g., BORME-A-2026-111-50).
        seccion: "I" (Sección A) or "II" (Sección B).

    Returns:
        A list of EdictRecord objects (possibly empty).
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.error("Failed to parse BORME doc XML: %s", doc_id)
        return []

    # Metadata
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
        elif tag == "url_pdf" and not pdf_url:
            pdf_url = (el.text or "").strip()

    texto_el = root.find(".//texto")
    paragraphs = []
    if texto_el is not None:
        for p in texto_el.findall("p"):
            paragraphs.append((p.text or "").strip())
        if not paragraphs:
            # Fallback: treat the whole body as one blob
            blob = ET.tostring(texto_el, encoding="unicode", method="text").strip()
            if blob:
                paragraphs = [blob]

    records: list[EdictRecord] = []

    if seccion == "I":
        # Pair company-header paragraphs with the following act paragraph.
        i = 0
        idx = 0
        n = len(paragraphs)
        while i < n:
            header = _COMPANY_HEADER_RE.match(paragraphs[i])
            if not header:
                i += 1
                continue
            company = header.group(2).strip()
            act_text = paragraphs[i + 1] if i + 1 < n else ""
            i += 2
            if not DEATH_KEYWORDS_RE.search(act_text):
                continue
            idx += 1
            records.append(
                _build_borme_record(
                    company=company,
                    act_text=act_text,
                    doc_id=doc_id,
                    seccion=seccion,
                    pub_date=pub_date,
                    pdf_url=pdf_url,
                    suffix=f"-{idx}",
                )
            )
    else:
        # Sección B: single act document.
        body_text = " ".join(paragraphs)
        if DEATH_KEYWORDS_RE.search(f"{title} {body_text}"):
            records.append(
                _build_borme_record(
                    company=title,
                    act_text=body_text,
                    doc_id=doc_id,
                    seccion=seccion,
                    pub_date=pub_date,
                    pdf_url=pdf_url,
                )
            )

    return records


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

            parsed = parse_borme_document(doc_xml, doc_id, item["seccion"])
            for record in parsed:
                records.append(record)
                logger.info(
                    "BORME lead: %s [%s] (id=%s, type=%s)",
                    record.causante or "?",
                    record.source,
                    record.source_id,
                    record.edict_type,
                )

        current += timedelta(days=1)

    logger.info("BORME scrape complete: %d records", len(records))
    return records
