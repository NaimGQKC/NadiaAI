"""Catastro public API client — enriches parcels by referencia catastral.

Uses the Sede Electrónica del Catastro SOAP/REST web services.
Only accesses public (non-protected) fields: address, m², year, use class.
Does NOT access titular name or valor catastral (protected, requires certificate).
"""

import contextlib
import logging
import sqlite3
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import requests

from nadia_ai.config import CATASTRO_CACHE_DAYS
from nadia_ai.db import insert_edict, insert_person, upsert_parcel
from nadia_ai.models import EdictRecord, ParcelInfo

logger = logging.getLogger("nadia_ai.catastro")

# Catastro SOAP endpoint for lookup by referencia catastral
CATASTRO_DNPRC_URL = (
    "https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx/Consulta_DNPRC"
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
