"""Scraper for CEE Aragón (Registro de Certificación de Eficiencia Energética).

The Aragón government publishes energy efficiency certificates for buildings
at https://aplicaciones.aragon.es/regcee/consulta.xhtml. A new energy
certificate is a high-intent signal for imminent residential listings — the
owner is preparing the property for sale or rent (legally required in Spain).

Implementation notes (verified against the live form, 2026-06):
  * The page is a single MyFaces/JSF form with id ``consultasForm``.
  * The real search fields are ``consultasForm:provSearch`` (province SELECT:
    Zaragoza = option value "4") and ``consultasForm:muniSearch`` (municipality
    SELECT, AJAX-populated; "Cualquiera" = "1").
  * Submission is a STANDARD (non-AJAX) form POST using the ``Buscar`` submit
    button (``consultasForm:j_idt34``); the server replies with a full HTML
    page, NOT a ``<partial-response>`` XML fragment.
  * The results table (``<table class="izquierda ancho100">``) has columns:
    Certificado | Tipo Edificio | Estado Edificio | Provincia | Municipio | Ver.
    It carries NO cadastral reference, NO street address and NO date — those
    live only behind per-certificate detail postbacks. We therefore emit
    list-level Tier-B records keyed on the certificate number, whose prefix
    encodes the inscription year (e.g. ``2026ZEVV-000233711`` → 2026).
  * Results are returned newest-first (descending certificate number), so we
    page a bounded number of pages to capture the freshest certificates.

These are NOT inheritance edicts — they're energy certificate registrations.
Leads from this source are Tier B (address/RC resolved later, no causante).
"""

import hashlib
import logging
import re
from datetime import UTC, datetime

import requests
from bs4 import BeautifulSoup

from nadia_ai.models import EdictRecord

logger = logging.getLogger("nadia_ai.scrapers.cee")

CONSULTA_URL = "https://aplicaciones.aragon.es/regcee/consulta.xhtml"
FORM_ID = "consultasForm"

# Province option values in the provSearch <select>.
PROV_ZARAGOZA = "4"
PROV_HUESCA = "2"
PROV_TERUEL = "3"
MUNI_ANY = "1"

# How many result pages (20 rows each) to walk. The list is newest-first, so a
# handful of pages covers the most recent certificates without hammering the
# server (the full set is ~100k rows / ~5.5k pages).
MAX_PAGES = 5

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 NadiaAI/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
    "Accept-Language": "es-ES,es;q=0.9",
})

# Certificate numbers look like "2026ZEVV-000233711"; the leading 4 digits are
# the inscription year, then the province letter (Z = Zaragoza), type code, etc.
CERT_RE = re.compile(r"^(\d{4})[A-Z]")


def _get_viewstate(html: str) -> str | None:
    """Extract javax.faces.ViewState from a JSF response."""
    soup = BeautifulSoup(html, "html.parser")
    vs = soup.find("input", {"name": "javax.faces.ViewState"})
    if vs and vs.get("value"):
        return vs["value"]
    m = re.search(r'name="javax\.faces\.ViewState"[^>]*value="([^"]*)"', html)
    return m.group(1) if m else None


def _form_action(html: str) -> str:
    """Absolute action URL for consultasForm (carries the jsessionid)."""
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", {"id": FORM_ID}) or soup.find("form")
    action = form.get("action") if form else None
    if action:
        if action.startswith("http"):
            return action
        return "https://aplicaciones.aragon.es" + action
    return CONSULTA_URL


def _base_fields(province: str, viewstate: str) -> dict:
    """Common form fields shared by the search and pagination posts."""
    return {
        FORM_ID: FORM_ID,
        f"{FORM_ID}:certSearch": "",
        f"{FORM_ID}:refcatSearch": "",
        f"{FORM_ID}:provSearch": province,
        f"{FORM_ID}:muniSearch": MUNI_ANY,
        f"{FORM_ID}:nomViaSearch": "",
        f"{FORM_ID}:numeroVia": "",
        "javax.faces.ViewState": viewstate,
    }


def _make_source_id(cert_number: str) -> str:
    return hashlib.md5(f"cee:{cert_number}".encode()).hexdigest()[:12]


def _cert_year(cert_number: str) -> str | None:
    m = CERT_RE.match(cert_number)
    return m.group(1) if m else None


def _find_results_table(soup: BeautifulSoup):
    """Locate the results table by its 'Certificado' header."""
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        header = [c.get_text(strip=True).lower() for c in rows[0].find_all(["th", "td"])]
        if header and header[0].startswith("certificado"):
            return table
    return None


def _parse_results_page(html: str) -> list[EdictRecord]:
    soup = BeautifulSoup(html, "html.parser")
    table = _find_results_table(soup)
    if table is None:
        return []

    records: list[EdictRecord] = []
    for row in table.find_all("tr")[1:]:
        cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
        if len(cells) < 5:
            continue

        cert_number = cells[0].strip()
        tipo = cells[1]
        estado = cells[2]
        provincia = cells[3]
        municipio = cells[4]
        if not cert_number:
            continue

        year = _cert_year(cert_number)
        published_at = None
        if year:
            try:
                published_at = datetime(int(year), 1, 1, tzinfo=UTC)
            except ValueError:
                published_at = None

        addr_parts = [p for p in (tipo, estado) if p]
        composite = " — ".join(addr_parts) if addr_parts else None

        records.append(
            EdictRecord(
                source="cee",
                source_id=_make_source_id(cert_number),
                edict_type="certificado_energetico",
                published_at=published_at,
                source_url=CONSULTA_URL,
                referencia_catastral=None,
                address=composite,
                localidad=municipio.title() if municipio else (provincia.title() or "Zaragoza"),
            )
        )
    return records


def scrape_cee(province: str = PROV_ZARAGOZA, max_pages: int = MAX_PAGES) -> list[EdictRecord]:
    """Scrape CEE Aragón energy certificates (Zaragoza province by default).

    Args:
        province: provSearch option value ("4" = Zaragoza, "2" = Huesca,
            "3" = Teruel).
        max_pages: number of newest-first result pages to walk (20 rows each).

    Returns:
        List of EdictRecord objects with source="cee" (newest certificates
        first), deduped by certificate number.
    """
    # Step 1: GET the form page → ViewState + jsessionid action URL.
    try:
        logger.info("CEE: Fetching consulta form page")
        form_resp = SESSION.get(CONSULTA_URL, timeout=30)
        form_resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("CEE: Failed to fetch consulta form: %s", e)
        return []

    viewstate = _get_viewstate(form_resp.text)
    action = _form_action(form_resp.text)
    if not viewstate:
        logger.error("CEE: Could not extract javax.faces.ViewState — form may have changed")
        return []
    logger.info("CEE: ViewState ok (%d chars), action=%s", len(viewstate), action)

    # Step 2: standard (non-AJAX) POST with the Buscar submit button.
    data = _base_fields(province, viewstate)
    data[f"{FORM_ID}:j_idt34"] = "Buscar"
    try:
        logger.info("CEE: Submitting search (province=%s)", province)
        resp = SESSION.post(
            action,
            data=data,
            headers={"Referer": CONSULTA_URL, "Content-Type": "application/x-www-form-urlencoded"},
            timeout=60,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("CEE: Search POST failed: %s", e)
        return []

    records: list[EdictRecord] = []
    seen: set[str] = set()

    def _absorb(html: str) -> int:
        added = 0
        for rec in _parse_results_page(html):
            if rec.source_id in seen:
                continue
            seen.add(rec.source_id)
            records.append(rec)
            added += 1
        return added

    n = _absorb(resp.text)
    logger.info("CEE: page 1 → %d records", n)
    if n == 0:
        logger.warning("CEE: results page had no rows — selectors or form may have changed")
        return []

    # Step 3: paginate via the 'next' image button. ViewState rotates each post.
    current_html = resp.text
    for page in range(2, max_pages + 1):
        vs = _get_viewstate(current_html)
        if not vs:
            break
        page_data = _base_fields(province, vs)
        page_data[f"{FORM_ID}:j_idt63_acsc_btn_next.x"] = "5"
        page_data[f"{FORM_ID}:j_idt63_acsc_btn_next.y"] = "5"
        try:
            resp = SESSION.post(
                action,
                data=page_data,
                headers={"Referer": CONSULTA_URL,
                         "Content-Type": "application/x-www-form-urlencoded"},
                timeout=60,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("CEE: pagination POST failed at page %d: %s", page, e)
            break
        added = _absorb(resp.text)
        logger.info("CEE: page %d → %d new records", page, added)
        current_html = resp.text
        if added == 0:
            break

    logger.info("CEE scrape complete: %d records", len(records))
    return records
