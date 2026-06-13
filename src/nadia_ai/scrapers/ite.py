"""Scraper for ITE Zaragoza (Inspección Técnica de Edificios).

STATUS: NO PUBLIC DATASET EXISTS (verified 2026-06).

Buildings with an unfavorable ITE face mandatory capital expenditures, which
makes those owners strong listing leads. Unfortunately Zaragoza does NOT
publish a per-building ITE results dataset:

  * The ITE service page (https://www.zaragoza.es/sede/servicio/tramite/9203)
    links to "Datos Abiertos" → catalog id 289
    (https://www.zaragoza.es/sede/servicio/catalogo/289.json). That dataset is
    the generic "Trámites y Servicios" procedure catalogue — it describes the
    ITE *procedure*, and contains zero inspection records (no addresses, no
    dictámenes, no cadastral refs).
  * Individual inspection results are explicitly handled as protected personal
    data (see the "Protección de datos" RAT entries
    /sede/servicio/rat/tratamiento/496 and /498), so they are not opened.
  * IDEZar's WFS node (http://idezar.zaragoza.es/inspire-node/services/wfs)
    only publishes INSPIRE address reference data — there is no ITE / building
    conservation-status layer.
  * The Aragón open-data portal only exposes a dataset of *certifying
    technicians/companies*, not building inspection outcomes.

The old candidate JSON endpoints (…/urbanismo-infraestructuras/ite.json, etc.)
404 because they never existed as data APIs. We keep a single cheap probe of
the catalog endpoint so this turns itself back on automatically if a real ITE
results distribution is ever published, but the expected, correct behaviour
today is a graceful no-op returning [].
"""

import hashlib
import logging
from datetime import UTC, datetime

import requests

from nadia_ai.models import EdictRecord

logger = logging.getLogger("nadia_ai.scrapers.ite")

# The only ITE-linked open-data endpoint that actually resolves. It currently
# serves the procedure catalogue (no inspection rows); we probe it so a future
# real distribution is detected without code changes.
CATALOG_URL = "https://www.zaragoza.es/sede/servicio/catalogo/289.json"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 NadiaAI/1.0",
    "Accept": "application/json",
})

DESFAVORABLE_KEYWORDS = {"desfavorable", "negativo", "no apto", "deficiente"}


def _is_desfavorable(dictamen: str) -> bool:
    if not dictamen:
        return False
    return any(kw in dictamen.lower().strip() for kw in DESFAVORABLE_KEYWORDS)


def _make_source_id(address: str, year: str) -> str:
    return hashlib.md5(f"ite:{address}:{year}".encode()).hexdigest()[:12]


def _parse_inspection_records(records: list[dict]) -> list[EdictRecord]:
    """Parse real ITE inspection rows (if a dataset ever appears) into edicts.

    Only emits buildings with an unfavorable dictamen and a usable address or
    cadastral reference.
    """
    edicts: list[EdictRecord] = []
    seen: set[str] = set()

    for rec in records:
        if isinstance(rec.get("properties"), dict):
            rec = rec["properties"]

        dictamen = rec.get("dictamen") or rec.get("resultado") or rec.get("calificacion") or ""
        if not _is_desfavorable(dictamen):
            continue

        direccion = rec.get("direccion") or rec.get("address") or rec.get("calle") or ""
        rc = rec.get("referencia_catastral") or rec.get("clave_catastro") or ""
        if not direccion and not rc:
            continue

        year = str(rec.get("anyo") or rec.get("year") or rec.get("fecha") or "")[:4]
        source_id = _make_source_id((direccion[:80] or rc), year or "unknown")
        if source_id in seen:
            continue
        seen.add(source_id)

        pub_date = None
        fecha_raw = rec.get("fecha") or ""
        if isinstance(fecha_raw, str) and len(fecha_raw) >= 10:
            try:
                pub_date = datetime.fromisoformat(fecha_raw[:10]).replace(tzinfo=UTC)
            except (ValueError, TypeError):
                pass

        addr_parts = [direccion] if direccion else []
        addr_parts.append(f"ITE: {dictamen}")
        if year:
            addr_parts.append(f"Año: {year}")

        edicts.append(
            EdictRecord(
                source="ite",
                source_id=source_id,
                edict_type="ite_desfavorable",
                published_at=pub_date,
                source_url="https://www.zaragoza.es/sede/servicio/tramite/9203",
                referencia_catastral=rc.upper() if rc else None,
                address=" — ".join(addr_parts),
                localidad="Zaragoza",
            )
        )
        logger.info("ITE desfavorable: %s | %s | %s", (direccion or "-")[:50], dictamen, year or "-")

    return edicts


def _looks_like_inspection_dataset(data) -> list[dict]:
    """Return inspection-shaped rows from a catalog payload, else []."""
    rows: list[dict] = []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("result") or data.get("records") or data.get("features") or []
    if not isinstance(rows, list):
        return []
    # An inspection row must expose at least one of these fields. The current
    # catalog (procedure metadata) has none of them, so this stays empty.
    keys = {"dictamen", "resultado", "calificacion", "direccion", "referencia_catastral"}
    return [r for r in rows if isinstance(r, dict) and keys & set(r.keys())]


def scrape_ite() -> list[EdictRecord]:
    """Scrape Zaragoza ITE building inspections.

    No open ITE results dataset is published (the data is protected), so this
    probes the one resolving catalog endpoint and returns [] unless a real
    inspection distribution appears there in the future.
    """
    try:
        resp = SESSION.get(CATALOG_URL, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.info("ITE: catalog probe failed (%s) — no data source available", e)
        return []

    rows = _looks_like_inspection_dataset(data)
    if not rows:
        logger.info(
            "ITE: no public inspection dataset (catalog 289 carries procedure "
            "metadata only; ITE results are protected) — 0 records"
        )
        return []

    edicts = _parse_inspection_records(rows)
    logger.info("ITE scrape complete: %d desfavorable records", len(edicts))
    return edicts
