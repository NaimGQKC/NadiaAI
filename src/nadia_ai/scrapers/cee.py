"""Scraper for CEE Aragón (Registro de Certificación de Eficiencia Energética).

The Aragón government publishes energy efficiency certificates for buildings
at https://aplicaciones.aragon.es/regcee/consulta.xhtml. A new energy
certificate is a high-intent signal for imminent residential listings — the
owner is preparing the property for sale or rent (legally required in Spain).

Strategy:
  1. GET the JSF form page → extract javax.faces.ViewState + JSESSIONID
  2. POST the form with municipality="Zaragoza"
  3. Parse the HTML results table
  4. Filter by inscription date (last N days)
  5. Return EdictRecord objects

These are NOT inheritance edicts — they're energy certificate registrations.
Leads from this source are Tier B (address/RC, no causante).
"""

import hashlib
import logging
import re
from datetime import UTC, datetime, timedelta

import requests
from bs4 import BeautifulSoup

from nadia_ai.models import EdictRecord

logger = logging.getLogger("nadia_ai.scrapers.cee")

CONSULTA_URL = "https://aplicaciones.aragon.es/regcee/consulta.xhtml"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "NadiaAI/1.0 (real-estate lead pipeline; polite scraper)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
    "Accept-Language": "es-ES,es;q=0.9",
})

# Cadastral reference pattern (20-char standard format)
RC_PATTERN = re.compile(r"\b(\d{7}[A-Z]{2}\d{4}[A-Z]\d{4}[A-Z]{2})\b", re.IGNORECASE)

# Date patterns: DD/MM/YYYY or YYYY-MM-DD
DATE_PATTERN_DMY = re.compile(r"(\d{2}/\d{2}/\d{4})")
DATE_PATTERN_ISO = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _extract_viewstate(html: str) -> str | None:
    """Extract javax.faces.ViewState from the JSF form page."""
    soup = BeautifulSoup(html, "html.parser")
    vs_input = soup.find("input", {"name": "javax.faces.ViewState"})
    if vs_input:
        return vs_input.get("value", "")
    # Fallback: regex search
    m = re.search(
        r'name="javax\.faces\.ViewState"\s+value="([^"]+)"', html
    )
    return m.group(1) if m else None


def _extract_form_id(html: str) -> str:
    """Extract the main form ID from the JSF page."""
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    if form and form.get("id"):
        return form["id"]
    return "j_idt6"  # Common default for JSF forms


def _parse_date(text: str) -> datetime | None:
    """Try to parse a date string in DD/MM/YYYY or YYYY-MM-DD format."""
    m = DATE_PATTERN_DMY.search(text)
    if m:
        try:
            return datetime.strptime(m.group(1), "%d/%m/%Y").replace(tzinfo=UTC)
        except ValueError:
            pass
    m = DATE_PATTERN_ISO.search(text)
    if m:
        try:
            return datetime.fromisoformat(m.group(1)).replace(tzinfo=UTC)
        except ValueError:
            pass
    return None


def _make_source_id(ref_catastral: str, inscription_year: str) -> str:
    """Generate a stable source_id from RC + inscription year.

    Using RC + year ensures that a property getting a new certificate
    years later is recognized as a new event for the same property.
    """
    key = f"cee:{ref_catastral}:{inscription_year}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _parse_results_table(html: str, cutoff: datetime) -> list[EdictRecord]:
    """Parse the CEE results table from the consulta page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    records: list[EdictRecord] = []
    seen_ids: set[str] = set()

    # Find all data tables — the results are typically in a <table> with class
    # containing "data" or "list" or within a panel
    tables = soup.find_all("table")
    if not tables:
        # Try finding results in a <div> with datalist/results
        logger.info("CEE: No tables found in results page")
        return records

    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        # Detect header row
        headers = []
        header_row = rows[0]
        for th in header_row.find_all(["th", "td"]):
            headers.append(th.get_text(strip=True).lower())

        if not headers:
            continue

        # Map column indices by keyword matching
        col_map = {}
        for idx, h in enumerate(headers):
            if "catastral" in h or "referencia" in h:
                col_map["rc"] = idx
            elif "inscripci" in h or "fecha" in h or "registro" in h:
                col_map["fecha"] = idx
            elif "calificaci" in h or "energ" in h or "clase" in h:
                col_map["calificacion"] = idx
            elif "direcci" in h or "ubicaci" in h or "localizaci" in h:
                col_map["direccion"] = idx
            elif "municipio" in h or "localidad" in h:
                col_map["municipio"] = idx

        # Need at least RC or address to be useful
        if "rc" not in col_map and "direccion" not in col_map:
            continue

        # Parse data rows
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < max(col_map.values(), default=0) + 1:
                continue

            def _cell(key: str) -> str:
                idx = col_map.get(key)
                if idx is not None and idx < len(cells):
                    return cells[idx].get_text(strip=True)
                return ""

            rc = _cell("rc").upper().replace(" ", "")
            fecha_str = _cell("fecha")
            calificacion = _cell("calificacion").upper()
            direccion = _cell("direccion")
            municipio = _cell("municipio") or "Zaragoza"

            # Validate RC format if present
            if rc and not RC_PATTERN.match(rc):
                # Try to find an RC in the cell text
                rc_match = RC_PATTERN.search(rc)
                rc = rc_match.group(1).upper() if rc_match else ""

            # Parse inscription date
            fecha = _parse_date(fecha_str) if fecha_str else None
            if fecha and fecha < cutoff:
                continue  # Older than our cutoff

            # Build composite address
            addr_parts = []
            if direccion:
                addr_parts.append(direccion)
            if calificacion:
                addr_parts.append(f"Calificación: {calificacion}")
            composite_address = " — ".join(addr_parts) if addr_parts else None

            if not rc and not composite_address:
                continue  # No useful data

            inscription_year = fecha.strftime("%Y") if fecha else "unknown"
            source_id = _make_source_id(rc or (direccion or "")[:50], inscription_year)

            if source_id in seen_ids:
                continue
            seen_ids.add(source_id)

            record = EdictRecord(
                source="cee",
                source_id=source_id,
                edict_type="certificado_energetico",
                published_at=fecha,
                source_url=CONSULTA_URL,
                referencia_catastral=rc or None,
                address=composite_address,
                localidad=municipio,
            )
            records.append(record)
            logger.info(
                "CEE: %s | %s | %s | %s",
                rc or "-",
                calificacion or "-",
                (direccion or "-")[:60],
                fecha_str or "-",
            )

    return records


def scrape_cee(since_days: int = 90) -> list[EdictRecord]:
    """Scrape CEE Aragón energy certificates for Zaragoza municipality.

    Args:
        since_days: Only include certificates inscribed in the last N days.

    Returns:
        List of EdictRecord objects with source="cee".
    """
    cutoff = datetime.now(UTC) - timedelta(days=since_days)

    # Step 1: GET the form page to extract JSF state
    try:
        logger.info("CEE: Fetching consulta form page")
        form_resp = SESSION.get(CONSULTA_URL, timeout=30)
        form_resp.raise_for_status()
        form_resp.encoding = "utf-8"
    except requests.RequestException as e:
        logger.error("CEE: Failed to fetch consulta form: %s", e)
        return []

    viewstate = _extract_viewstate(form_resp.text)
    form_id = _extract_form_id(form_resp.text)

    if not viewstate:
        logger.error("CEE: Could not extract javax.faces.ViewState — JSF form may have changed")
        return []

    logger.info("CEE: Extracted ViewState (%d chars), form_id=%s", len(viewstate), form_id)

    # Step 2: POST the search form with municipality = Zaragoza
    # JSF forms use the form ID as prefix for field names
    post_data = {
        "javax.faces.ViewState": viewstate,
        "javax.faces.partial.ajax": "true",
        "javax.faces.source": f"{form_id}:btnBuscar",
        "javax.faces.partial.execute": "@all",
        "javax.faces.partial.render": "@all",
        f"{form_id}": form_id,
        f"{form_id}:btnBuscar": f"{form_id}:btnBuscar",
    }

    # Try common field name patterns for municipality input
    municipality_field_names = [
        f"{form_id}:municipio",
        f"{form_id}:municipio_input",
        f"{form_id}:txtMunicipio",
        f"{form_id}:selectMunicipio",
        f"{form_id}:localidad",
        f"{form_id}:inputMunicipio",
    ]
    for field_name in municipality_field_names:
        post_data[field_name] = "Zaragoza"

    # Province field (Zaragoza = 50)
    province_field_names = [
        f"{form_id}:provincia",
        f"{form_id}:selectProvincia",
        f"{form_id}:provincia_input",
    ]
    for field_name in province_field_names:
        post_data[field_name] = "Zaragoza"

    # Also try without AJAX (standard form submit)
    post_headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Faces-Request": "partial/ajax",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": CONSULTA_URL,
    }

    try:
        logger.info("CEE: Submitting search form (AJAX POST)")
        search_resp = SESSION.post(
            CONSULTA_URL,
            data=post_data,
            headers=post_headers,
            timeout=60,
        )
        search_resp.raise_for_status()
        search_resp.encoding = "utf-8"
    except requests.RequestException as e:
        logger.error("CEE: Form submission failed: %s", e)
        # Try standard (non-AJAX) POST as fallback
        try:
            logger.info("CEE: Retrying with standard POST (non-AJAX)")
            post_data_simple = {
                "javax.faces.ViewState": viewstate,
                f"{form_id}": form_id,
                f"{form_id}:btnBuscar": "Buscar",
            }
            for field_name in municipality_field_names:
                post_data_simple[field_name] = "Zaragoza"
            for field_name in province_field_names:
                post_data_simple[field_name] = "Zaragoza"

            search_resp = SESSION.post(
                CONSULTA_URL,
                data=post_data_simple,
                timeout=60,
            )
            search_resp.raise_for_status()
            search_resp.encoding = "utf-8"
        except requests.RequestException as e2:
            logger.error("CEE: Standard POST also failed: %s", e2)
            return []

    result_html = search_resp.text

    # Check if we got an AJAX partial response — extract the HTML content
    if "<partial-response>" in result_html or "CDATA" in result_html:
        # Extract HTML from AJAX response: <![CDATA[...]]>
        cdata_match = re.search(r"<!\[CDATA\[(.*?)\]\]>", result_html, re.DOTALL)
        if cdata_match:
            result_html = cdata_match.group(1)

    # Step 3: Parse results
    records = _parse_results_table(result_html, cutoff)

    # If AJAX returned no results, try fetching the page again (server may
    # have updated the view state and the results are now rendered)
    if not records:
        try:
            logger.info("CEE: Re-fetching page after form submission")
            page_resp = SESSION.get(CONSULTA_URL, timeout=30)
            page_resp.raise_for_status()
            page_resp.encoding = "utf-8"
            records = _parse_results_table(page_resp.text, cutoff)
        except requests.RequestException:
            pass

    logger.info(
        "CEE scrape complete: %d records (last %d days)", len(records), since_days
    )
    return records
