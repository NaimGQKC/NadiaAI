"""Catastro public API client — enriches parcels by referencia catastral.

Uses the Sede Electrónica del Catastro SOAP/REST web services.
Only accesses public (non-protected) fields: address, m², year, use class.
Does NOT access titular name or valor catastral (protected, requires certificate).
"""

import contextlib
import logging
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import requests

from nadia_ai.config import CATASTRO_CACHE_DAYS
from nadia_ai.db import insert_edict, insert_person, upsert_parcel
from nadia_ai.merge import strip_accents
from nadia_ai.models import EdictRecord, ParcelInfo

logger = logging.getLogger("nadia_ai.catastro")

# Catastro SOAP endpoint for lookup by referencia catastral
CATASTRO_DNPRC_URL = (
    "https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx/Consulta_DNPRC"
)

# Catastro endpoint for resolving a street address -> referencia catastral
CATASTRO_DNPLOC_URL = (
    "https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx/Consulta_DNPLOC"
)

# Street-type abbreviation -> Catastro "Sigla" codes. Catastro's callejero is
# strict about the via type, so we map common Spanish prefixes to its codes.
_VIA_SIGLA = {
    "calle": "CL",
    "c/": "CL",
    "c.": "CL",
    "cl": "CL",
    "avenida": "AV",
    "avda": "AV",
    "avda.": "AV",
    "av": "AV",
    "av.": "AV",
    "plaza": "PZ",
    "pza": "PZ",
    "pza.": "PZ",
    "pl": "PZ",
    "plza": "PZ",
    "paseo": "PS",
    "ps": "PS",
    "po": "PS",
    "camino": "CM",
    "cmno": "CM",
    "cº": "CM",
    "carretera": "CR",
    "ctra": "CR",
    "ctra.": "CR",
    "cra": "CR",
    "ronda": "RD",
    "rda": "RD",
    "rda.": "RD",
    "travesia": "TR",
    "travesía": "TR",
    "trav": "TR",
    "glorieta": "GL",
    "gta": "GL",
    "pasaje": "PJ",
    "pje": "PJ",
    "pje.": "PJ",
    "via": "CL",
    "urbanizacion": "UR",
    "urbanización": "UR",
    "urb": "UR",
    "urb.": "UR",
    "poligono": "PG",
    "polígono": "PG",
    "pol": "PG",
    "pol.": "PG",
    "barrio": "BO",
    "bo": "BO",
    "rambla": "RB",
    "callejon": "CJ",
    "callejón": "CJ",
}

# Capture an optional via-type prefix, the street name, and a house number. The
# number prefix tolerates the full Spanish set ("número/núm./nº/n.º/n.").
_ADDR_RE = re.compile(
    r"^\s*(?P<via>[A-Za-zÁÉÍÓÚÑáéíóúñ./]+)?\.?\s*"
    r"(?P<name>.+?)[,\s]+(?:n[uú]m(?:ero)?\.?\s*|n[.ºo°]+\s*)?(?P<num>\d+)",
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": "NadiaAI/0.1 (real-estate-research)",
    "Accept": "application/xml",
}

# XML namespace used in Catastro responses
NS = {"cat": "http://www.catastro.meh.es/"}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF = [1, 3, 10]  # seconds


def lookup_by_rc(rc: str) -> ParcelInfo | None:
    """Look up property data by referencia catastral.

    Returns ParcelInfo with public fields, or None if not found.
    Implements exponential backoff on server errors.
    """
    # The RC is split into two parts for the API:
    # Provincia (2 chars) + Municipio (3 chars) + rest
    # But Consulta_DNPRC takes the full RC in two params:
    # Provincia (first 7 chars) and the rest
    if len(rc) < 14:
        logger.warning("Invalid referencia catastral format: %s", rc)
        return None

    params = {
        "Provincia": "",
        "Municipio": "",
        "SiglasTipoVia": "",
        "NombreVia": "",
        "Numero": "",
        "RC": rc,
    }

    for attempt in range(MAX_RETRIES):
        try:
            resp = SESSION.get(CATASTRO_DNPRC_URL, params=params, timeout=15)

            if resp.status_code == 404:
                logger.info("Catastro: RC %s not found (404)", rc)
                return None

            if resp.status_code >= 500:
                if attempt < MAX_RETRIES - 1:
                    import time

                    wait = RETRY_BACKOFF[attempt]
                    logger.warning(
                        "Catastro %d error, retrying in %ds (attempt %d/%d)",
                        resp.status_code,
                        wait,
                        attempt + 1,
                        MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue
                resp.raise_for_status()

            resp.raise_for_status()
            return _parse_response(rc, resp.text)

        except requests.Timeout:
            if attempt < MAX_RETRIES - 1:
                import time

                time.sleep(RETRY_BACKOFF[attempt])
                continue
            logger.error("Catastro timeout after %d retries for RC %s", MAX_RETRIES, rc)
            return None

        except requests.RequestException as e:
            logger.error("Catastro request failed for RC %s: %s", rc, e)
            return None

    return None


def _parse_address(direccion: str) -> tuple[str, str, str] | None:
    """Split a free-text Spanish address into (sigla, street_name, number).

    Returns None if no house number can be found — Catastro DNPLOC requires one,
    so an address with only a city/street and no number is not resolvable.
    """
    if not direccion:
        return None
    # Take only the part before the first city/postcode separator to avoid
    # feeding "Calle X 7, 50005 Zaragoza — Comercio" wholesale.
    head = re.split(r"\b\d{5}\b", direccion)[0]
    m = _ADDR_RE.match(head)
    if not m:
        return None
    via_raw = (m.group("via") or "").strip().lower().rstrip(".")
    name = (m.group("name") or "").strip(" ,.-")
    num = m.group("num")
    if not name or not num:
        return None
    sigla = _VIA_SIGLA.get(via_raw, "")
    # If the captured "via" wasn't a recognized type, it's part of the name.
    if not sigla and via_raw:
        name = f"{via_raw} {name}".strip()
        sigla = "CL"  # default to Calle
    elif not sigla:
        sigla = "CL"
    # Catastro's callejero stores names without a leading article
    # ("Avenida de Aragón" -> via=AV, name="ARAGON"). Strip it.
    name = re.sub(r"^(?:de\s+la\s+|de\s+los\s+|de\s+las\s+|del\s+|de\s+|la\s+|el\s+|los\s+|las\s+)", "", name, flags=re.IGNORECASE)
    return sigla, name.upper(), num


def lookup_rc_by_address(
    provincia: str, municipio: str, direccion: str
) -> str | None:
    """Resolve a referencia catastral from a street address via Consulta_DNPLOC.

    Catastro's callejero is strict (exact via spelling, house number required),
    so this resolves only a subset of well-formed urban addresses. Returns the
    14-char parcel RC (pc1+pc2) or None.
    """
    parsed = _parse_address(direccion)
    if not parsed or not provincia or not municipio:
        return None
    sigla, calle, numero = parsed

    params = {
        "Provincia": strip_accents(provincia).upper(),
        "Municipio": strip_accents(municipio).upper(),
        "Sigla": sigla,
        "Calle": strip_accents(calle).upper(),
        "Numero": numero,
        "Bloque": "",
        "Escalera": "",
        "Planta": "",
        "Puerta": "",
    }

    for attempt in range(MAX_RETRIES):
        try:
            resp = SESSION.get(CATASTRO_DNPLOC_URL, params=params, timeout=20)
            if resp.status_code >= 500:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BACKOFF[attempt])
                    continue
                return None
            resp.raise_for_status()
            return _parse_rc_from_loc(resp.text)
        except requests.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF[attempt])
                continue
            logger.info("Catastro DNPLOC failed for '%s': %s", direccion[:50], e)
            return None
    return None


def _parse_rc_from_loc(xml_text: str) -> str | None:
    """Extract the first referencia catastral (pc1+pc2) from a DNPLOC response."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    # Detect an explicit error response (<cuerr>N</cuerr>, e.g. "LA VÍA NO EXISTE").
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "cuerr" and (el.text or "").strip() not in ("", "0"):
            return None
    pc1 = pc2 = None
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "pc1" and pc1 is None:
            pc1 = (el.text or "").strip()
        elif tag == "pc2" and pc2 is None:
            pc2 = (el.text or "").strip()
        if pc1 and pc2:
            break
    if pc1 and pc2:
        return f"{pc1}{pc2}"
    return None


def resolve_lead_addresses(conn: sqlite3.Connection, limit: int = 200) -> int:
    """Backfill referencia_catastral for leads that have a street address but no RC.

    Most leads only carry a `localidad` (city) and therefore cannot be geocoded.
    This targets the minority with a parseable street-level `direccion`. Returns
    the count of leads for which an RC was resolved.
    """
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, direccion, localidad
        FROM leads
        WHERE (referencia_catastral IS NULL OR referencia_catastral = '')
          AND direccion IS NOT NULL AND direccion != ''
          AND direccion GLOB '*[0-9]*'
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    resolved = 0
    for row in rows:
        direccion = row["direccion"]
        # Prefer an explicit municipality; fall back to localidad, then default.
        municipio = (row["localidad"] or "").strip()
        if not municipio:
            # Try to read a city out of the address tail (e.g. "..., Zaragoza").
            municipio = _city_from_address(direccion) or ""
        if not municipio:
            continue
        provincia = _PROVINCIA_BY_CITY.get(strip_accents(municipio).lower(), municipio)

        rc = lookup_rc_by_address(provincia, municipio, direccion)
        if not rc:
            continue
        conn.execute(
            "UPDATE leads SET referencia_catastral = ?, ref_catastral = ?, "
            "last_updated_at = datetime('now') WHERE id = ?",
            (rc, rc, row["id"]),
        )
        resolved += 1
        logger.info("Resolved RC %s for lead %d from address", rc, row["id"])

    conn.commit()
    logger.info("Catastro address resolution: %d/%d leads resolved", resolved, len(rows))
    return resolved


# City (accent-stripped, lowercase) → its PROVINCE. Catastro's DNPLOC needs the
# real province, not the city — the old map used city==province, so every non-capital
# town (Calatayud, Monzón, Alcañiz…) failed. Aragón is the operating territory, so
# its municipalities are mapped to the right province; national capitals close the
# rest of the common cases.
_PROVINCIA_BY_CITY = {
    # ── Zaragoza province ──
    "zaragoza": "ZARAGOZA", "calatayud": "ZARAGOZA", "utebo": "ZARAGOZA",
    "ejea de los caballeros": "ZARAGOZA", "tarazona": "ZARAGOZA", "caspe": "ZARAGOZA",
    "zuera": "ZARAGOZA", "cuarte de huerva": "ZARAGOZA", "alagon": "ZARAGOZA",
    "la almunia de dona godina": "ZARAGOZA", "borja": "ZARAGOZA", "daroca": "ZARAGOZA",
    "epila": "ZARAGOZA", "fuentes de ebro": "ZARAGOZA", "gallur": "ZARAGOZA",
    "maria de huerva": "ZARAGOZA", "pinseque": "ZARAGOZA", "pedrola": "ZARAGOZA",
    "villanueva de gallego": "ZARAGOZA", "carinena": "ZARAGOZA", "sadaba": "ZARAGOZA",
    "tauste": "ZARAGOZA", "ricla": "ZARAGOZA",
    # ── Huesca province ──
    "huesca": "HUESCA", "monzon": "HUESCA", "barbastro": "HUESCA", "jaca": "HUESCA",
    "fraga": "HUESCA", "sabinanigo": "HUESCA", "binefar": "HUESCA", "sarinena": "HUESCA",
    "graus": "HUESCA", "ainsa": "HUESCA", "tamarite de litera": "HUESCA",
    # ── Teruel province ──
    "teruel": "TERUEL", "alcaniz": "TERUEL", "andorra": "TERUEL", "calamocha": "TERUEL",
    "utrillas": "TERUEL", "alcorisa": "TERUEL", "mora de rubielos": "TERUEL",
    "calanda": "TERUEL",
    # ── National capitals (province == city) ──
    "madrid": "MADRID", "barcelona": "BARCELONA", "valencia": "VALENCIA",
    "sevilla": "SEVILLA", "malaga": "MALAGA", "granada": "GRANADA",
    "salamanca": "SALAMANCA", "cadiz": "CADIZ", "tarragona": "TARRAGONA",
    "bilbao": "BIZKAIA", "logrono": "LA RIOJA", "pamplona": "NAVARRA",
    "lleida": "LLEIDA", "soria": "SORIA", "guadalajara": "GUADALAJARA",
}


def _city_from_address(direccion: str) -> str | None:
    """Best-effort city extraction from the tail of an address string.

    Longest names first so "ejea de los caballeros" wins over a stray "ejea" and a
    short token can't shadow a more specific multi-word municipality.
    """
    if not direccion:
        return None
    norm = strip_accents(direccion).lower()
    for city in sorted(_PROVINCIA_BY_CITY, key=len, reverse=True):
        if city in norm:
            return city.title()
    return None


def _parse_response(rc: str, xml_text: str) -> ParcelInfo | None:
    """Parse Catastro XML response into a ParcelInfo."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.error("Failed to parse Catastro XML for RC %s: %s", rc, e)
        return None

    # Try to find the property data in the response
    # Catastro response structure varies — try multiple paths
    info = ParcelInfo(referencia_catastral=rc)

    # Extract address components
    locs = root.findall(".//" + "locs") or root.findall(".//{http://www.catastro.meh.es/}locs")
    if not locs:
        # Try alternative path
        locs = root.findall(".//locs")

    # Extract fields by iterating all elements (handles namespaced and plain tags)
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag

        if tag == "ldt":  # Full address
            info.address = (el.text or "").strip()
        elif tag == "sfc":  # Superficie construida (m²)
            with contextlib.suppress(ValueError, TypeError):
                info.m2 = float(el.text)
        elif tag == "ant":  # Antigüedad (year of construction)
            with contextlib.suppress(ValueError, TypeError):
                info.year_built = int(el.text)
        elif tag == "luso":  # Uso local (use description)
            info.use_class = (el.text or "").strip()

    # Infer neighborhood from address
    if info.address:
        info.neighborhood = _infer_neighborhood(info.address)

    if info.address or info.m2:
        logger.info("Catastro enriched RC %s: %s, %s m²", rc, info.address, info.m2)
        return info

    logger.info("Catastro returned no useful data for RC %s", rc)
    return None


def _infer_neighborhood(address: str) -> str:
    """Infer Zaragoza neighborhood from address string.

    Simple heuristic based on known barrio names in addresses.
    """
    barrios = [
        "Casco Histórico",
        "Centro",
        "Delicias",
        "Universidad",
        "San José",
        "Las Fuentes",
        "La Almozara",
        "Oliver-Valdefierro",
        "Torrero-La Paz",
        "Actur-Rey Fernando",
        "El Rabal",
        "Casablanca",
        "Santa Isabel",
        "Miralbueno",
        "Sur",
        "Margen Izquierda",
        "Rural Norte",
        "Rural Oeste",
        "Rural Sur",
    ]
    address_upper = address.upper()
    for barrio in barrios:
        if barrio.upper() in address_upper:
            return barrio
    return ""


def _is_cached(conn: sqlite3.Connection, rc: str) -> bool:
    """Check if we have a recent Catastro enrichment for this RC."""
    row = conn.execute(
        "SELECT last_updated_at, enrichment_pending FROM parcels WHERE referencia_catastral = ?",
        (rc,),
    ).fetchone()
    if not row:
        return False
    if row["enrichment_pending"]:
        return False
    try:
        updated = datetime.fromisoformat(row["last_updated_at"])
        cutoff = datetime.now(UTC) - timedelta(days=CATASTRO_CACHE_DAYS)
        # Handle naive datetime from SQLite
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        return updated > cutoff
    except (ValueError, TypeError):
        return False


def enrich_and_persist(conn: sqlite3.Connection, records: list[EdictRecord]) -> int:
    """Enrich edict records via Catastro and persist to database.

    For each record:
    1. Upsert placeholder parcel (needed for FK)
    2. Insert the edict (skip if duplicate)
    3. Insert person records with TTL
    4. Enrich parcel via Catastro (use cache if fresh)

    Returns count of successfully enriched records.
    """
    enriched_count = 0

    for record in records:
        # Upsert placeholder parcel first (FK constraint requires it)
        if record.referencia_catastral:
            upsert_parcel(
                conn,
                {
                    "referencia_catastral": record.referencia_catastral,
                    "address": None,
                    "neighborhood": None,
                    "m2": None,
                    "year_built": None,
                    "use_class": None,
                    "enrichment_pending": 1,
                },
            )

        # Insert edict
        edict_data = {
            "source": record.source,
            "source_id": record.source_id,
            "referencia_catastral": record.referencia_catastral,
            "edict_type": record.edict_type,
            "published_at": record.published_at.isoformat() if record.published_at else None,
            "source_url": record.source_url,
            "address": record.address,
        }
        edict_id = insert_edict(conn, edict_data)
        if edict_id is None:
            # Duplicate — already processed
            continue

        # Insert person records
        if record.causante:
            insert_person(conn, edict_id, "causante", record.causante)
        for petitioner in record.petitioners:
            insert_person(conn, edict_id, "petitioner", petitioner)

        # Enrich via Catastro
        if not record.referencia_catastral:
            continue

        if _is_cached(conn, record.referencia_catastral):
            logger.debug("Using cached Catastro data for RC %s", record.referencia_catastral)
            enriched_count += 1
            continue

        parcel_info = lookup_by_rc(record.referencia_catastral)

        parcel_data = {
            "referencia_catastral": record.referencia_catastral,
            "address": parcel_info.address if parcel_info else None,
            "neighborhood": parcel_info.neighborhood if parcel_info else None,
            "m2": parcel_info.m2 if parcel_info else None,
            "year_built": parcel_info.year_built if parcel_info else None,
            "use_class": parcel_info.use_class if parcel_info else None,
            "enrichment_pending": 0 if parcel_info else 1,
        }
        upsert_parcel(conn, parcel_data)

        if parcel_info:
            enriched_count += 1
        else:
            logger.warning(
                "Catastro enrichment failed for RC %s — flagged as pending",
                record.referencia_catastral,
            )

    return enriched_count
