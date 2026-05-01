"""Enrichment modules: cross-reference existing leads with external data.

Two enrichment sources:

1. **Subastas BOE** — voluntary auctions (judicial JV + notarial NV) from
   subastas.boe.es for Zaragoza province. Matched to leads by referencia
   catastral or normalized address. When a lead's property appears in an
   active voluntary auction, outreach is still allowed but the agent should
   verify the heirs' disposition capacity.

2. **Zaragoza Open Data Licencias de Obra** — building permits from the
   city open-data catalogue. Matched by normalized address. Recent works
   near a lead's property add actionable context.

Enrichment adds CONTEXT to existing leads; it never creates new leads.
"""

import logging
import re
import sqlite3
from datetime import UTC, datetime

import requests
from bs4 import BeautifulSoup

from nadia_ai.merge import normalize_address, strip_accents

logger = logging.getLogger("nadia_ai.enrichment")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USER_AGENT = "Mozilla/5.0 (compatible; NadiaAI/2.0)"

SUBASTAS_LIST_URLS = [
    (
        "https://subastas.boe.es/subastas_ava.php?provincia=50&tipoSubasta=JV",
        "JV",
    ),
    (
        "https://subastas.boe.es/subastas_ava.php?provincia=50&tipoSubasta=NV",
        "NV",
    ),
]

SUBASTAS_DETAIL_URL = "https://subastas.boe.es/detalleSubasta.php?idSub={id_sub}"

OBRAS_CATALOG_URL = (
    "https://www.zaragoza.es/sede/servicio/licencia-obra.json?rows=500&start=0"
)

SUBASTAS_TIMEOUT = 60  # seconds — their server is slow
OBRAS_TIMEOUT = 30

# ---------------------------------------------------------------------------
# Schema SQL
# ---------------------------------------------------------------------------

ENRICHMENT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS enrichment_subastas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subasta_id TEXT UNIQUE,
    referencia_catastral TEXT,
    direccion TEXT,
    address_norm TEXT,
    valor_tasacion REAL,
    procedimiento TEXT,
    tipo_subasta TEXT,
    url TEXT,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS enrichment_obras (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expediente TEXT UNIQUE,
    referencia_catastral TEXT,
    direccion TEXT,
    address_norm TEXT,
    tipo_licencia TEXT,
    fecha TEXT,
    url TEXT,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_enrich_sub_rc
    ON enrichment_subastas(referencia_catastral);
CREATE INDEX IF NOT EXISTS idx_enrich_sub_addr
    ON enrichment_subastas(address_norm);
CREATE INDEX IF NOT EXISTS idx_enrich_obras_addr
    ON enrichment_obras(address_norm);
"""

# ---------------------------------------------------------------------------
# Shared session
# ---------------------------------------------------------------------------


def _make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------


def init_enrichment_schema(conn: sqlite3.Connection) -> None:
    """Create enrichment tables if they don't exist."""
    conn.executescript(ENRICHMENT_SCHEMA_SQL)
    conn.commit()
    logger.info("Enrichment schema initialized")


# ---------------------------------------------------------------------------
# Subastas BOE — scraping
# ---------------------------------------------------------------------------

# Pattern for subasta IDs in result links
_SUBASTA_ID_RE = re.compile(r"idSub=([^&\"']+)")

# Pattern for referencia catastral (20-char standard format)
_RC_RE = re.compile(r"\b(\d{7}[A-Z]{2}\d{4}[A-Z]\d{4}[A-Z]{2})\b", re.IGNORECASE)

# Pattern for valor tasacion
_VALOR_RE = re.compile(r"valor\s+(?:de\s+)?tasaci[oó]n[:\s]*([0-9.,]+)", re.IGNORECASE)


def _parse_subastas_list(html: str, tipo: str) -> list[str]:
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
    # Look for pagination links — typically "Siguiente" or ">"
    for link in soup.find_all("a", href=True):
        text = link.get_text(strip=True).lower()
        if text in ("siguiente", ">", ">>", "sig"):
            href = link["href"]
            if href.startswith("http"):
                return href
            # Relative URL
            if href.startswith("/"):
                from urllib.parse import urljoin

                return urljoin(base_url, href)
            return base_url.rsplit("/", 1)[0] + "/" + href
    # Also check for page=N pattern in pagination
    soup_pages = soup.find_all("a", href=re.compile(r"page=\d+"))
    if soup_pages:
        # Find current page number from URL or content
        current_page = 1
        page_match = re.search(r"page=(\d+)", base_url)
        if page_match:
            current_page = int(page_match.group(1))
        for link in soup_pages:
            href = link["href"]
            pm = re.search(r"page=(\d+)", href)
            if pm and int(pm.group(1)) == current_page + 1:
                if href.startswith("http"):
                    return href
                from urllib.parse import urljoin

                return urljoin(base_url, href)
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

    # Try to extract address and procedimiento from labeled table rows
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

    # Fallback: look for address in definition lists
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

    # Fallback: look for labeled spans/divs
    if not result["direccion"]:
        for el in soup.find_all(["span", "div", "p"]):
            el_text = el.get_text(strip=True)
            if el_text.lower().startswith("direcci") and ":" in el_text:
                _, _, addr = el_text.partition(":")
                addr = addr.strip()
                if addr and len(addr) >= 5:
                    result["direccion"] = addr
                    break

    return result


def fetch_subastas(conn: sqlite3.Connection) -> int:
    """Fetch and store subastas data. Returns count of new entries."""
    init_enrichment_schema(conn)
    session = _make_session()
    new_count = 0
    max_pages = 10  # Safety limit per subasta type

    for base_url, tipo in SUBASTAS_LIST_URLS:
        current_url: str | None = base_url
        page = 0

        while current_url and page < max_pages:
            page += 1
            try:
                logger.info(
                    "Fetching subastas %s page %d: %s", tipo, page, current_url
                )
                resp = session.get(current_url, timeout=SUBASTAS_TIMEOUT)
                resp.raise_for_status()
                resp.encoding = "utf-8"
            except requests.RequestException as e:
                logger.error("Subastas %s page %d failed: %s", tipo, page, e)
                break

            sub_ids = _parse_subastas_list(resp.text, tipo)
            logger.info("Subastas %s page %d: %d results", tipo, page, len(sub_ids))

            if not sub_ids:
                break

            for sub_id in sub_ids:
                # Skip if already stored
                existing = conn.execute(
                    "SELECT 1 FROM enrichment_subastas WHERE subasta_id = ?",
                    (sub_id,),
                ).fetchone()
                if existing:
                    continue

                detail_url = SUBASTAS_DETAIL_URL.format(id_sub=sub_id)
                try:
                    detail_resp = session.get(detail_url, timeout=SUBASTAS_TIMEOUT)
                    detail_resp.raise_for_status()
                    detail_resp.encoding = "utf-8"
                except requests.RequestException as e:
                    logger.warning(
                        "Subasta detail %s failed: %s", sub_id, e
                    )
                    continue

                detail = _parse_subasta_detail(detail_resp.text)
                addr_norm = normalize_address(detail.get("direccion"))

                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO enrichment_subastas
                           (subasta_id, referencia_catastral, direccion,
                            address_norm, valor_tasacion, procedimiento,
                            tipo_subasta, url)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            sub_id,
                            detail.get("referencia_catastral"),
                            detail.get("direccion"),
                            addr_norm,
                            detail.get("valor_tasacion"),
                            detail.get("procedimiento"),
                            tipo,
                            detail_url,
                        ),
                    )
                    if conn.total_changes:
                        new_count += 1
                        logger.info(
                            "Stored subasta %s (%s) — RC=%s addr=%s",
                            sub_id,
                            tipo,
                            detail.get("referencia_catastral") or "-",
                            (detail.get("direccion") or "-")[:60],
                        )
                except sqlite3.Error as e:
                    logger.warning("DB insert subasta %s failed: %s", sub_id, e)

            # Paginate
            current_url = _get_next_page_url(resp.text, current_url)

    conn.commit()
    logger.info("Subastas fetch complete: %d new entries", new_count)
    return new_count


# ---------------------------------------------------------------------------
# Obras / Licencias — Zaragoza Open Data
# ---------------------------------------------------------------------------


def fetch_obras(conn: sqlite3.Connection) -> int:
    """Fetch and store obras licencias data. Returns count of new entries."""
    init_enrichment_schema(conn)
    session = _make_session()
    session.headers["Accept"] = "application/json"
    new_count = 0

    try:
        logger.info("Fetching obras licencias from Zaragoza Open Data")
        resp = session.get(OBRAS_CATALOG_URL, timeout=OBRAS_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Obras fetch failed: %s", e)
        return 0

    try:
        data = resp.json()
    except ValueError:
        logger.error("Obras endpoint returned non-JSON response")
        return 0

    # The API may return the data in various structures. Handle the common
    # patterns: a top-level list, or a dict with "result"/"results"/"rows".
    records: list[dict] = []
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        for key in ("result", "results", "rows", "data", "features"):
            if key in data and isinstance(data[key], list):
                records = data[key]
                break
        if not records:
            # Maybe the whole dict is a single record or has nested structure
            if "properties" in data:
                records = [data]
            else:
                logger.warning(
                    "Obras response has unexpected structure: keys=%s",
                    list(data.keys())[:10],
                )
                return 0

    logger.info("Obras: %d records received", len(records))

    for rec in records:
        # Adapt field names — the open-data API may use different casing
        # or nested structures. We try several common patterns.
        if "properties" in rec and isinstance(rec["properties"], dict):
            rec = rec["properties"]

        # Zaragoza licencia-obra.json uses nested expediente[] array
        exp_list = rec.get("expediente")
        if isinstance(exp_list, list) and exp_list:
            exp_obj = exp_list[0] if isinstance(exp_list[0], dict) else {}
        elif isinstance(exp_list, dict):
            exp_obj = exp_list
        else:
            exp_obj = {}

        expediente = (
            exp_obj.get("id")
            or rec.get("id")
            or rec.get("ID")
        )
        if not expediente:
            continue
        expediente = str(expediente).strip()

        direccion = (
            rec.get("direccion")
            or rec.get("Direccion")
            or rec.get("address")
            or rec.get("via")
            or ""
        )
        # Extract tipo from nested datosObra or top-level
        datos_obra = exp_obj.get("datosObra", {}) if exp_obj else {}
        if isinstance(datos_obra, list) and datos_obra:
            datos_obra = datos_obra[0] if isinstance(datos_obra[0], dict) else {}
        tipo_licencia = (
            (datos_obra.get("tipoObra") if isinstance(datos_obra, dict) else None)
            or exp_obj.get("tipo")
            or rec.get("tipo_licencia")
            or rec.get("tipo")
            or ""
        )
        fecha = (
            exp_obj.get("creationDate")
            or rec.get("fecha")
            or rec.get("fecha_resolucion")
            or ""
        )
        url = rec.get("url") or rec.get("link") or ""

        # Use claveCatastro for address matching if available
        clave_catastro = rec.get("claveCatastro") or ""
        addr_norm = normalize_address(direccion) if direccion else None

        try:
            conn.execute(
                """INSERT OR IGNORE INTO enrichment_obras
                   (expediente, referencia_catastral, direccion, address_norm,
                    tipo_licencia, fecha, url)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    expediente,
                    clave_catastro or None,
                    direccion,
                    addr_norm,
                    tipo_licencia,
                    str(fecha) if fecha else None,
                    url,
                ),
            )
            # Check if the row was actually inserted (not a duplicate)
            if conn.total_changes:
                new_count += 1
        except sqlite3.Error as e:
            logger.warning("DB insert obra %s failed: %s", expediente, e)

    conn.commit()
    logger.info("Obras fetch complete: %d new entries", new_count)
    return new_count


# ---------------------------------------------------------------------------
# Cross-join enrichment: subastas -> leads
# ---------------------------------------------------------------------------


def _ensure_lead_columns(conn: sqlite3.Connection) -> None:
    """Add enrichment columns to the leads table if they don't exist yet.

    SQLite doesn't support IF NOT EXISTS on ALTER TABLE, so we check
    the table schema first.
    """
    cursor = conn.execute("PRAGMA table_info(leads)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    if "subasta_activa" not in existing_columns:
        conn.execute("ALTER TABLE leads ADD COLUMN subasta_activa TEXT DEFAULT ''")
        logger.info("Added column 'subasta_activa' to leads table")

    if "obras_recientes" not in existing_columns:
        conn.execute("ALTER TABLE leads ADD COLUMN obras_recientes TEXT DEFAULT ''")
        logger.info("Added column 'obras_recientes' to leads table")

    conn.commit()


def enrich_leads_from_subastas(conn: sqlite3.Connection) -> int:
    """Cross-join subastas to leads. Returns count of leads enriched."""
    init_enrichment_schema(conn)
    _ensure_lead_columns(conn)
    enriched = 0

    # Match by referencia catastral
    rows_rc = conn.execute(
        """SELECT l.id AS lead_id, s.url, s.tipo_subasta
           FROM leads l
           INNER JOIN enrichment_subastas s
               ON l.ref_catastral IS NOT NULL
               AND l.ref_catastral != ''
               AND l.ref_catastral = s.referencia_catastral
           WHERE (l.subasta_activa IS NULL OR l.subasta_activa = '')"""
    ).fetchall()

    # Match by normalized address
    rows_addr = conn.execute(
        """SELECT l.id AS lead_id, s.url, s.tipo_subasta
           FROM leads l
           INNER JOIN enrichment_subastas s
               ON l.address_norm IS NOT NULL
               AND l.address_norm != ''
               AND l.address_norm = s.address_norm
           WHERE (l.subasta_activa IS NULL OR l.subasta_activa = '')
             AND l.id NOT IN (
                 SELECT l2.id FROM leads l2
                 INNER JOIN enrichment_subastas s2
                     ON l2.ref_catastral IS NOT NULL
                     AND l2.ref_catastral != ''
                     AND l2.ref_catastral = s2.referencia_catastral
                 WHERE (l2.subasta_activa IS NULL OR l2.subasta_activa = '')
             )"""
    ).fetchall()

    all_matches = list(rows_rc) + list(rows_addr)
    seen_lead_ids: set[int] = set()

    for row in all_matches:
        lead_id = row["lead_id"]
        if lead_id in seen_lead_ids:
            continue
        seen_lead_ids.add(lead_id)

        subasta_url = row["url"] or ""
        note = (
            "propiedad en subasta voluntaria — verificar capacidad "
            "disposicion de los herederos antes de visita"
        )

        # Append to existing outreach_notes rather than overwriting
        existing = conn.execute(
            "SELECT outreach_notes FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
        current_notes = (existing["outreach_notes"] or "") if existing else ""
        if note not in current_notes:
            new_notes = (
                f"{current_notes}; {note}" if current_notes else note
            )
        else:
            new_notes = current_notes

        conn.execute(
            """UPDATE leads
               SET subasta_activa = ?,
                   outreach_notes = ?,
                   last_updated_at = datetime('now')
               WHERE id = ?""",
            (subasta_url, new_notes, lead_id),
        )
        enriched += 1
        logger.info("Lead %d enriched with subasta: %s", lead_id, subasta_url[:80])

    conn.commit()
    logger.info("Subastas enrichment: %d leads enriched", enriched)
    return enriched


# ---------------------------------------------------------------------------
# Cross-join enrichment: obras -> leads
# ---------------------------------------------------------------------------


def enrich_leads_from_obras(conn: sqlite3.Connection) -> int:
    """Cross-join obras to leads. Returns count of leads enriched."""
    init_enrichment_schema(conn)
    _ensure_lead_columns(conn)
    enriched = 0

    rows = conn.execute(
        """SELECT l.id AS lead_id, o.tipo_licencia, o.fecha
           FROM leads l
           INNER JOIN enrichment_obras o
               ON l.address_norm IS NOT NULL
               AND l.address_norm != ''
               AND l.address_norm = o.address_norm
           WHERE (l.obras_recientes IS NULL OR l.obras_recientes = '')"""
    ).fetchall()

    # Group by lead_id — a lead may match multiple obras
    from collections import defaultdict

    obras_by_lead: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        obras_by_lead[row["lead_id"]].append(
            {"tipo": row["tipo_licencia"] or "?", "fecha": row["fecha"] or "?"}
        )

    for lead_id, obras_list in obras_by_lead.items():
        # Build a compact summary string
        parts = [f"{o['tipo']} ({o['fecha']})" for o in obras_list[:5]]
        summary = "; ".join(parts)

        conn.execute(
            """UPDATE leads
               SET obras_recientes = ?,
                   last_updated_at = datetime('now')
               WHERE id = ?""",
            (summary, lead_id),
        )
        enriched += 1
        logger.info("Lead %d enriched with obras: %s", lead_id, summary[:80])

    conn.commit()
    logger.info("Obras enrichment: %d leads enriched", enriched)
    return enriched
