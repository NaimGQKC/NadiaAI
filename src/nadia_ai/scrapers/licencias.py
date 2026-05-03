"""Scraper for Zaragoza city Licencias de Obra (building permits) JSON API.

The Zaragoza open-data portal publishes all building permits with catastral
references and permit types (obra, derribo, ocupacion). Demolition and
construction permits signal properties about to come to market — valuable
lead signals for a real-estate agent.

API: GET https://www.zaragoza.es/sede/servicio/licencia-obra.json
Supports: rows, start, fl (field list). No server-side date filter.
Total dataset is ~2000 records, so we paginate through all and filter
client-side for expedientes within the last N days.
"""

import hashlib
import logging
from datetime import UTC, datetime, timedelta

import requests

from nadia_ai.models import EdictRecord

logger = logging.getLogger("nadia_ai.scrapers.licencias")

API_URL = "https://www.zaragoza.es/sede/servicio/licencia-obra.json"
PAGE_SIZE = 50
# Fields we need from the API
FIELDS = "id,claveCatastro,expediente,geometry"
# Permit types we care about
WANTED_TYPES = {"derribo", "obra"}

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "NadiaAI/1.0 (real-estate lead pipeline; polite scraper)",
    "Accept": "application/json",
})


def _expediente_date(exp: dict) -> datetime | None:
    """Parse creationDate from an expediente entry."""
    raw = exp.get("creationDate")
    if not raw:
        return None
    try:
        # Format: "2026-03-02T00:00:00"
        return datetime.fromisoformat(raw).replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def _make_source_id(licencia_id: str, exp_idx: int) -> str:
    """Generate a stable source_id from the licencia id + expediente index."""
    key = f"licencia:{licencia_id}:{exp_idx}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _edict_type_for(tipo: str) -> str:
    """Map API tipo to our edict_type."""
    if tipo == "derribo":
        return "licencia_derribo"
    return "licencia_obra"


def _describe_permit(exp: dict) -> str:
    """Build a human-readable description from expediente fields."""
    parts = []
    contenido = exp.get("contenido")
    if contenido:
        parts.append(contenido.strip())
    obs = exp.get("observaciones")
    if obs:
        parts.append(obs.strip())
    proc = exp.get("procedimiento")
    if proc:
        parts.append(proc.strip())
    return " / ".join(parts) if parts else exp.get("tipo", "obra").upper()


def scrape_licencias(since_days: int = 90) -> list[EdictRecord]:
    """Fetch building permits from Zaragoza open-data API.

    Paginates through the full dataset and returns EdictRecord objects
    for permits (derribo/obra) with expedientes created in the last
    `since_days` days.

    Args:
        since_days: Only include expedientes newer than this many days.

    Returns:
        List of EdictRecord objects with source="licencias".
    """
    cutoff = datetime.now(UTC) - timedelta(days=since_days)
    all_records: list[EdictRecord] = []
    seen_ids: set[str] = set()
    start = 0

    while True:
        params = {
            "rows": PAGE_SIZE,
            "start": start,
            "fl": FIELDS,
        }
        try:
            resp = SESSION.get(API_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.error("Licencias API fetch failed (start=%d): %s", start, e)
            break
        except ValueError as e:
            logger.error("Licencias API invalid JSON (start=%d): %s", start, e)
            break

        results = data.get("result", [])
        if not results:
            break

        total = data.get("totalCount", 0)
        logger.info(
            "Licencias page start=%d: %d results (total=%d)",
            start, len(results), total,
        )

        for item in results:
            licencia_id = item.get("id", "")
            catastro = item.get("claveCatastro", "")
            expedientes = item.get("expediente", [])

            if not expedientes:
                continue

            for idx, exp in enumerate(expedientes):
                tipo = exp.get("tipo", "")
                if tipo not in WANTED_TYPES:
                    continue

                exp_date = _expediente_date(exp)
                if exp_date is None or exp_date < cutoff:
                    continue

                source_id = _make_source_id(licencia_id, idx)
                if source_id in seen_ids:
                    continue
                seen_ids.add(source_id)

                edict_type = _edict_type_for(tipo)
                description = _describe_permit(exp)
                api_url = f"{API_URL}?id={licencia_id}"

                record = EdictRecord(
                    source="licencias",
                    source_id=source_id,
                    edict_type=edict_type,
                    published_at=exp_date,
                    source_url=api_url,
                    referencia_catastral=catastro or None,
                    address=description,  # No street address in API; use permit description
                    localidad="Zaragoza",
                )
                all_records.append(record)
                logger.info(
                    "Licencia: %s | %s | %s | %s",
                    catastro, edict_type, description,
                    exp_date.strftime("%Y-%m-%d"),
                )

        start += PAGE_SIZE
        if start >= total:
            break

    logger.info("Licencias scrape complete: %d records (last %d days)", len(all_records), since_days)
    return all_records
