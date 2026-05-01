"""Scraper for BOE (Boletín Oficial del Estado) — inheritance publications.

Two sub-sources:
1. TEJU (Tablón Edictal Judicial Único) — all judicial inheritance edicts
   for Zaragoza courts (declaración de herederos, herencias yacentes).
   HTML scraping of boe.es/buscar/edictos_judiciales.php
2. Section V — state-as-heir announcements from Ministerio de Hacienda
   with full property inventories. Uses BOE Open Data XML API.

Combined volume for Zaragoza province: 30-80+ per year.
"""

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import requests
from bs4 import BeautifulSoup

from nadia_ai.models import EdictRecord

logger = logging.getLogger("nadia_ai.scrapers.boe")

# --- TEJU (Judicial Edicts) ---
TEJU_SEARCH_URL = "https://www.boe.es/buscar/edictos_judiciales.php"

# --- BOE Section V (State-as-heir) ---
BOE_SUMARIO_URL = "https://boe.es/datosabiertos/api/boe/sumario/{date}"
BOE_DOC_XML_URL = "https://www.boe.es/diario_boe/xml.php?id={doc_id}"

HEADERS = {
    "User-Agent": "NadiaAI/0.1 (lead-generation research; contact: dev@example.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml",
    "Accept-Language": "es-ES,es;q=0.9",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Name extraction patterns
NAME_PATTERNS = [
    # "sucesion legal de D./D.a NAME a favor de"
    re.compile(
        r"sucesi[oó]n\s+legal\s+de\s+(?:D\.?a?\s+|DO[NÑ]A?\s+|D[ñÑ]a\.?\s+)?"
        r"([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑa-záéíóúñ][a-záéíóúñA-ZÁÉÍÓÚÑ]+){1,5})"
        r"\s+a\s+favor",
        re.IGNORECASE,
    ),
    # "herederos abintestato de D./Dña. NAME"
    re.compile(
        r"herederos\s+(?:abintestato\s+)?de\s+(?:D\.?a?\s+|DO[NÑ]A?\s+|D[ñÑ]a\.?\s+)?"
        r"([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑa-záéíóúñ][a-záéíóúñA-ZÁÉÍÓÚÑ]+){1,5})",
        re.IGNORECASE,
    ),
    # "declaración de herederos ... D./Dña. NAME"
    re.compile(
        r"declaraci[oó]n\s+de\s+herederos.*?(?:D\.?a?\s+|DO[NÑ]A?\s+|D[ñÑ]a\.?\s+)"
        r"([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑa-záéíóúñ][a-záéíóúñA-ZÁÉÍÓÚÑ]+){1,5})",
        re.IGNORECASE,
    ),
    # "fallecimiento de D./Dña. NAME"
    re.compile(
        r"(?:fallecimiento|defunci[oó]n)\s+de\s+(?:D\.?a?\s+|DO[NÑ]A?\s+|D[ñÑ]a\.?\s+)?"
        r"([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑa-záéíóúñ][a-záéíóúñA-ZÁÉÍÓÚÑ]+){1,5})",
        re.IGNORECASE,
    ),
    # "herencia yacente de NAME"
    re.compile(
        r"herencia\s+yacente\s+(?:de\s+)?(?:D\.?a?\s+|DO[NÑ]A?\s+|D[ñÑ]a\.?\s+)?"
        r"([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑa-záéíóúñ][a-záéíóúñA-ZÁÉÍÓÚÑ]+){1,5})",
        re.IGNORECASE,
    ),
]

# Cadastral reference pattern
RC_PATTERN = re.compile(r"\b(\d{7}[A-Z]{2}\d{4}[A-Z]\d{4}[A-Z]{2})\b", re.IGNORECASE)

# TEJU search queries
TEJU_QUERIES = [
    '"herederos abintestato"',
    '"declaracion de herederos"',
    '"herencia yacente"',
]


def _extract_name(text: str) -> str | None:
    """Extract deceased person's name from text."""
    for pattern in NAME_PATTERNS:
        m = pattern.search(text)
        if m:
            name = m.group(1).strip()
            if name.isupper():
                name = name.title()
            return name
    return None


# ── TEJU (Judicial Edicts) ──────────────────────────────────────────


def fetch_teju_search(query: str, since: datetime | None = None) -> str:
    """Search TEJU for judicial edicts. Returns HTML."""
    params = {
        "campo[0]": "DOC",
        "dato[0]": query,
        "operador[0]": "",
        "campo[1]": "DEM",
        "dato[1]": "Zaragoza",
        "operador[1]": "Y",
        "accion": "Buscar",
        "page_hits": "200",
    }
    if since:
        params["FechaPublicacionDesde"] = since.strftime("%d/%m/%Y")
        params["FechaPublicacionHasta"] = datetime.now(UTC).strftime("%d/%m/%Y")

    response = SESSION.get(TEJU_SEARCH_URL, params=params, timeout=30)
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def parse_teju_results(html: str) -> list[dict]:
    """Parse TEJU search results HTML into edict dicts."""
    soup = BeautifulSoup(html, "lxml")
    results = []

    # TEJU results appear in a list — look for result items
    # Each result typically has a link, publication date, court, and summary
    for item in soup.find_all(["li", "div", "tr"], class_=re.compile(r"result|dato")):
        text = item.get_text(separator=" ", strip=True)
        if len(text) < 20:
            continue

        link = item.find("a", href=True)
        url = ""
        if link:
            href = link["href"]
            url = href if href.startswith("http") else f"https://www.boe.es{href}"

        # Extract date
        date = None
        date_match = re.search(r"(\d{2}/\d{2}/\d{4})", text)
        if date_match:
            try:
                date = datetime.strptime(date_match.group(1), "%d/%m/%Y").replace(tzinfo=UTC)
            except ValueError:
                pass

        doc_id = ""
        id_match = re.search(r"(BOE-[A-Z]-\d{4}-\d+)", text + " " + url)
        if id_match:
            doc_id = id_match.group(1)
        elif url:
            doc_id = hashlib.md5(url.encode()).hexdigest()[:12]
        else:
            doc_id = hashlib.md5(text[:100].encode()).hexdigest()[:12]

        results.append({
            "title": text[:300],
            "url": url,
            "date": date,
            "doc_id": doc_id,
        })

    # Fallback: look for any links with BOE IDs
    if not results:
        for link in soup.find_all("a", href=True):
            href = link["href"]
            text = link.get_text(strip=True)
            if "BOE" in href or "edicto" in href.lower():
                url = href if href.startswith("http") else f"https://www.boe.es{href}"
                doc_id_match = re.search(r"(BOE-[A-Z]-\d{4}-\d+)", href)
                doc_id = doc_id_match.group(1) if doc_id_match else hashlib.md5(
                    href.encode()
                ).hexdigest()[:12]
                results.append({
                    "title": text[:300],
                    "url": url,
                    "date": None,
                    "doc_id": doc_id,
                })

    return results


def scrape_teju(since: datetime | None = None) -> list[EdictRecord]:
    """Scrape TEJU for Zaragoza judicial inheritance edicts."""
    if since is None:
        since = datetime.now(UTC) - timedelta(days=90)

    seen_ids = set()
    records = []

    for query in TEJU_QUERIES:
        try:
            html = fetch_teju_search(query, since=since)
            results = parse_teju_results(html)
            logger.info("TEJU query '%s': %d results", query, len(results))

            for r in results:
                if r["doc_id"] in seen_ids:
                    continue
                seen_ids.add(r["doc_id"])

                causante = _extract_name(r["title"])
                record = EdictRecord(
                    source="boe_teju",
                    source_id=r["doc_id"],
                    referencia_catastral=None,
                    edict_type="edicto_judicial_herederos",
                    published_at=r.get("date"),
                    source_url=r.get("url", ""),
                    causante=causante,
                )
                records.append(record)
                if causante:
                    logger.info("TEJU lead: %s (id=%s)", causante, r["doc_id"])

        except requests.RequestException as e:
            logger.error("TEJU query '%s' failed: %s", query, e)

    logger.info("TEJU scrape complete: %d unique records", len(records))
    return records


# ── BOE Section V (State-as-heir) ───────────────────────────────────


def fetch_boe_sumario(date: datetime) -> str:
    """Fetch the BOE daily summary XML for a given date."""
    url = BOE_SUMARIO_URL.format(date=date.strftime("%Y%m%d"))
    response = SESSION.get(url, timeout=15)
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def fetch_boe_doc_xml(doc_id: str) -> str:
    """Fetch a single BOE document XML by its ID (e.g., BOE-B-2026-11730)."""
    url = BOE_DOC_XML_URL.format(doc_id=doc_id)
    response = SESSION.get(url, timeout=15)
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def extract_section5_doc_ids(sumario_xml: str) -> list[str]:
    """Extract document IDs from Section V-B (Hacienda) in a BOE daily summary."""
    doc_ids = []
    try:
        root = ET.fromstring(sumario_xml)
    except ET.ParseError:
        return doc_ids

    # Walk the XML looking for Section V items from department 5140 (Hacienda)
    for item in root.iter("item"):
        item_id = item.get("id", "")
        # Check if it's a Section V item (BOE-B prefix)
        if not item_id.startswith("BOE-B"):
            continue

        # Check department code or title for inheritance keywords
        titulo_el = item.find("titulo")
        titulo = titulo_el.text if titulo_el is not None and titulo_el.text else ""

        dept_el = item.find("departamento")
        dept = dept_el.text if dept_el is not None and dept_el.text else ""

        keywords = ["sucesión", "sucesion", "abintestato", "herencia", "heredero"]
        combined = f"{titulo} {dept}".lower()
        if any(kw in combined for kw in keywords):
            doc_ids.append(item_id)

    return doc_ids


def parse_boe_document(xml_text: str, doc_id: str) -> EdictRecord | None:
    """Parse a BOE document XML into an EdictRecord."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.error("Failed to parse BOE doc XML: %s", doc_id)
        return None

    # Extract metadata
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

    # Extract full text
    text = ""
    texto_el = root.find(".//texto")
    if texto_el is not None:
        text = ET.tostring(texto_el, encoding="unicode", method="text")

    combined = f"{title} {text}"
    causante = _extract_name(combined)

    # Look for referencia catastral in the text (inventories often have them)
    ref_catastral = None
    rc_match = RC_PATTERN.search(combined)
    if rc_match:
        ref_catastral = rc_match.group(1).upper()

    source_url = pdf_url
    if not source_url:
        source_url = f"https://www.boe.es/diario_boe/txt.php?id={doc_id}"

    return EdictRecord(
        source="boe_secv",
        source_id=doc_id,
        referencia_catastral=ref_catastral,
        edict_type="sucesion_legal_estado",
        published_at=pub_date,
        source_url=source_url,
        causante=causante,
    )


def scrape_boe_section5(since: datetime | None = None) -> list[EdictRecord]:
    """Scrape BOE Section V for state-as-heir announcements.

    Checks daily summaries for the last N days for Hacienda inheritance entries.
    Low volume (1-5/year for Zaragoza) but includes full property inventories.
    """
    if since is None:
        since = datetime.now(UTC) - timedelta(days=90)

    records = []
    current = since
    end = datetime.now(UTC)

    while current <= end:
        try:
            sumario_xml = fetch_boe_sumario(current)
            doc_ids = extract_section5_doc_ids(sumario_xml)

            for doc_id in doc_ids:
                try:
                    doc_xml = fetch_boe_doc_xml(doc_id)
                    record = parse_boe_document(doc_xml, doc_id)
                    if record:
                        records.append(record)
                        logger.info(
                            "BOE Sec.V lead: %s (id=%s)", record.causante or "?", doc_id
                        )
                except requests.RequestException as e:
                    logger.error("Failed to fetch BOE doc %s: %s", doc_id, e)

        except requests.RequestException as e:
            # Weekends/holidays have no BOE — 404 is normal
            if "404" not in str(e):
                logger.debug("BOE sumario %s: %s", current.strftime("%Y%m%d"), e)

        current += timedelta(days=1)

    logger.info("BOE Section V scrape complete: %d records", len(records))
    return records


# ── Combined BOE scraper ────────────────────────────────────────────


def scrape_boe(since: datetime | None = None) -> list[EdictRecord]:
    """Scrape both BOE sources (TEJU + Section V) for inheritance edicts."""
    teju_records = scrape_teju(since=since)
    sec5_records = scrape_boe_section5(since=since)

    all_records = teju_records + sec5_records
    logger.info("BOE total: %d records (TEJU=%d, Sec.V=%d)",
                len(all_records), len(teju_records), len(sec5_records))
    return all_records
