"""Lead deduplication, merging, and tier classification.

After scrapers run and edicts are persisted, this module:
1. Matches new records against existing leads (by RC, name, or address)
2. Merges data from multiple sources into a single lead
3. Classifies each lead into tiers (A/B/C/X)
4. Sets outreach-legality flags
"""

import json
import logging
import re
import sqlite3
import unicodedata
from datetime import UTC, datetime

from nadia_ai.models import EdictRecord

logger = logging.getLogger("nadia_ai.merge")

LEADS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Merge keys (normalized, for matching)
    causante_norm TEXT,
    ref_catastral TEXT,
    address_norm TEXT,
    -- Best merged data
    causante TEXT DEFAULT '',
    direccion TEXT DEFAULT '',
    localidad TEXT DEFAULT '',
    fecha_fallecimiento TEXT DEFAULT '',
    fecha_nacimiento TEXT DEFAULT '',
    lugar_nacimiento TEXT DEFAULT '',
    lugar_fallecimiento TEXT DEFAULT '',
    referencia_catastral TEXT DEFAULT '',
    m2 REAL,
    year_built INTEGER,
    use_class TEXT DEFAULT '',
    neighborhood TEXT DEFAULT '',
    -- Classification
    tier TEXT NOT NULL DEFAULT 'C',
    outreach_allowed INTEGER NOT NULL DEFAULT 1,
    outreach_notes TEXT DEFAULT '',
    -- Tracking
    sources TEXT NOT NULL DEFAULT '[]',
    source_urls TEXT NOT NULL DEFAULT '[]',
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    -- Enrichment fields (populated by cross-join with enrichment tables)
    subasta_activa TEXT DEFAULT '',
    obras_recientes TEXT DEFAULT '',
    nif TEXT DEFAULT '',
    valor_tasacion REAL,
    procedimiento TEXT DEFAULT '',
    subsource TEXT DEFAULT '',
    juzgado TEXT DEFAULT '',
    -- Nadia's fields (manual, preserved across merges)
    estado TEXT DEFAULT 'Nuevo',
    notas TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS lead_edicts (
    lead_id INTEGER NOT NULL,
    edict_id INTEGER NOT NULL,
    PRIMARY KEY (lead_id, edict_id),
    FOREIGN KEY (lead_id) REFERENCES leads(id),
    FOREIGN KEY (edict_id) REFERENCES edicts(id)
);

CREATE INDEX IF NOT EXISTS idx_leads_causante_norm ON leads(causante_norm);
CREATE INDEX IF NOT EXISTS idx_leads_ref_catastral ON leads(ref_catastral);
CREATE INDEX IF NOT EXISTS idx_leads_address_norm ON leads(address_norm);
CREATE INDEX IF NOT EXISTS idx_leads_first_seen ON leads(first_seen_at);
"""

SOURCE_LABELS = {
    "tablon": "Tablón",
    "boa": "BOA",
    "bop": "BOP Zaragoza",
    "boe_teju": "BOE (TEJU)",
    "boe_secv": "BOE (Sec.V)",
    "borme_i": "BORME-I",
    "borme_ii": "BORME-II",
    "subastas": "Subastas BOE",
}

# Maps source to subsource code for finer-grained tracking
SUBSOURCE_CODES = {
    "tablon": "Tablón",
    "boa": "BOA-JD",
    "bop": "BOPZ",
    "boe_teju": "BOE-TEJU",
    "boe_secv": "BOE-V.B",
    "borme_i": "BORME-I",
    "borme_ii": "BORME-II",
}


# ── Normalization helpers ──────────────────────────────────────────


def strip_accents(s: str) -> str:
    """Remove diacritical marks from a string."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def normalize_name(name: str | None) -> str | None:
    """Normalize a person name for dedup matching."""
    if not name:
        return None
    s = strip_accents(name).lower().strip()
    # Strip honorifics
    s = re.sub(r"^(don|dona|d\.|dna\.?|da\.?)\s+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s if s else None


def normalize_address(address: str | None) -> str | None:
    """Normalize an address for dedup matching."""
    if not address:
        return None
    s = strip_accents(address).lower().strip()
    s = re.sub(
        r"^(calle|c/|cl\s|avda?\.?|avenida|plaza|pza\.?|paseo|camino|"
        r"ctra\.?|carretera|ronda|travesia|trav\.?|glorieta)\s+",
        "",
        s,
    )
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) >= 5 else None


# ── Tier and outreach classification ───────────────────────────────


def compute_tier(lead: dict) -> str:
    """Compute tier classification for a lead.

    A — name + address, actionable today, fresh (<6 months)
    B — name OR address (one missing but recoverable), or A-quality but stale (>6 months)
    C — neither name nor address but link works
    X — distress flag, enrichment only
    """
    sources = json.loads(lead.get("sources", "[]"))
    is_distress = any(
        "subasta" in s.lower() or "concurso" in s.lower() for s in sources
    )
    if is_distress:
        return "X"

    has_name = bool(lead.get("causante"))
    has_address = bool(lead.get("direccion"))

    # Check staleness (>6 months since first detection)
    is_stale = False
    first_seen = lead.get("first_seen_at")
    if first_seen:
        try:
            seen_dt = datetime.fromisoformat(first_seen)
            if seen_dt.tzinfo is None:
                seen_dt = seen_dt.replace(tzinfo=UTC)
            age_days = (datetime.now(UTC) - seen_dt).days
            is_stale = age_days > 180
        except (ValueError, TypeError):
            pass

    if has_name and has_address:
        return "B" if is_stale else "A"
    elif has_name or has_address:
        return "B"
    return "C"


def compute_outreach(lead: dict) -> tuple[bool, str]:
    """Compute outreach-legality flag and notes."""
    sources = json.loads(lead.get("sources", "[]"))

    distress_keywords = {"subasta", "concurso", "foreclosure"}
    if any(d in s.lower() for s in sources for d in distress_keywords):
        return False, "Fuente de dificultad financiera — solo contexto, no contacto directo"

    if any("borme" in s.lower() for s in sources):
        return True, "Contexto B2B — empresa, no persona física"

    return True, ""


# ── DB helpers ─────────────────────────────────────────────────────


_LEADS_MIGRATIONS = [
    ("subsource", "ALTER TABLE leads ADD COLUMN subsource TEXT DEFAULT ''"),
    ("subasta_activa", "ALTER TABLE leads ADD COLUMN subasta_activa TEXT DEFAULT ''"),
    ("obras_recientes", "ALTER TABLE leads ADD COLUMN obras_recientes TEXT DEFAULT ''"),
    ("nif", "ALTER TABLE leads ADD COLUMN nif TEXT DEFAULT ''"),
    ("valor_tasacion", "ALTER TABLE leads ADD COLUMN valor_tasacion REAL"),
    ("procedimiento", "ALTER TABLE leads ADD COLUMN procedimiento TEXT DEFAULT ''"),
    ("juzgado", "ALTER TABLE leads ADD COLUMN juzgado TEXT DEFAULT ''"),
]


def init_leads_schema(conn: sqlite3.Connection) -> None:
    """Create leads and lead_edicts tables if they don't exist, then migrate."""
    conn.executescript(LEADS_SCHEMA_SQL)
    # Migrate existing tables: add any missing columns
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(leads)")}
    for col_name, alter_sql in _LEADS_MIGRATIONS:
        if col_name not in existing_cols:
            conn.execute(alter_sql)
            logger.info("Migrated leads table: added column '%s'", col_name)
    conn.commit()


def _find_matching_lead(conn: sqlite3.Connection, record: EdictRecord) -> int | None:
    """Find an existing lead matching this record. Returns lead_id or None."""
    # Strong: referencia catastral
    if record.referencia_catastral:
        row = conn.execute(
            "SELECT id FROM leads WHERE ref_catastral = ? LIMIT 1",
            (record.referencia_catastral,),
        ).fetchone()
        if row:
            return row[0]

    # Medium: normalized causante name
    name_norm = normalize_name(record.causante)
    if name_norm:
        row = conn.execute(
            "SELECT id FROM leads WHERE causante_norm = ? LIMIT 1",
            (name_norm,),
        ).fetchone()
        if row:
            return row[0]

    # Weak: normalized address
    addr_norm = normalize_address(record.address)
    if addr_norm:
        row = conn.execute(
            "SELECT id FROM leads WHERE address_norm = ? LIMIT 1",
            (addr_norm,),
        ).fetchone()
        if row:
            return row[0]

    return None


def _get_edict_id(conn: sqlite3.Connection, source: str, source_id: str) -> int | None:
    """Get edict DB id by source + source_id."""
    row = conn.execute(
        "SELECT id FROM edicts WHERE source = ? AND source_id = ? LIMIT 1",
        (source, source_id),
    ).fetchone()
    return row[0] if row else None


def _get_parcel_data(conn: sqlite3.Connection, rc: str | None) -> dict:
    """Get parcel enrichment data by referencia catastral."""
    if not rc:
        return {}
    row = conn.execute(
        "SELECT address, neighborhood, m2, year_built, use_class "
        "FROM parcels WHERE referencia_catastral = ?",
        (rc,),
    ).fetchone()
    return dict(row) if row else {}


def _update_lead_classification(conn: sqlite3.Connection, lead_id: int) -> None:
    """Recompute tier and outreach for a lead."""
    lead = dict(conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone())
    tier = compute_tier(lead)
    ok, notes = compute_outreach(lead)
    conn.execute(
        "UPDATE leads SET tier = ?, outreach_allowed = ?, outreach_notes = ? WHERE id = ?",
        (tier, int(ok), notes, lead_id),
    )


# ── Main merge entry point ─────────────────────────────────────────


def merge_leads(conn: sqlite3.Connection, records: list[EdictRecord]) -> dict:
    """Merge scraped records into the leads table.

    For each record:
    1. Skip if no name AND no address (noise)
    2. Find matching lead by RC → name → address
    3. If found: merge data (COALESCE, add source)
    4. If not found: create new lead
    5. Classify tier and outreach
    6. Link edict to lead

    Returns stats dict: {created, merged, skipped}.
    """
    init_leads_schema(conn)
    stats = {"created": 0, "merged": 0, "skipped": 0}

    for record in records:
        if not record.causante and not record.address:
            stats["skipped"] += 1
            continue

        edict_id = _get_edict_id(conn, record.source, record.source_id)
        parcel = _get_parcel_data(conn, record.referencia_catastral)
        best_address = parcel.get("address") or record.address or ""
        source_label = SOURCE_LABELS.get(record.source, record.source)

        lead_id = _find_matching_lead(conn, record)

        if lead_id:
            _merge_into_existing(conn, lead_id, record, parcel, best_address, source_label)
            stats["merged"] += 1
        else:
            lead_id = _create_new_lead(conn, record, parcel, best_address, source_label)
            stats["created"] += 1

        _update_lead_classification(conn, lead_id)

        if edict_id:
            conn.execute(
                "INSERT OR IGNORE INTO lead_edicts (lead_id, edict_id) VALUES (?, ?)",
                (lead_id, edict_id),
            )

    conn.commit()

    logger.info(
        "Lead merge: %d created, %d merged, %d skipped",
        stats["created"],
        stats["merged"],
        stats["skipped"],
    )
    return stats


def _merge_into_existing(
    conn: sqlite3.Connection,
    lead_id: int,
    record: EdictRecord,
    parcel: dict,
    best_address: str,
    source_label: str,
) -> None:
    """Merge a new record into an existing lead (COALESCE — fill blanks)."""
    existing = dict(conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone())

    sources = json.loads(existing["sources"])
    if source_label not in sources:
        sources.append(source_label)

    source_urls = json.loads(existing["source_urls"])
    if record.source_url and record.source_url not in source_urls:
        source_urls.append(record.source_url)

    conn.execute(
        """UPDATE leads SET
            causante = COALESCE(NULLIF(?, ''), causante),
            causante_norm = COALESCE(?, causante_norm),
            direccion = COALESCE(NULLIF(?, ''), direccion),
            address_norm = COALESCE(?, address_norm),
            localidad = COALESCE(NULLIF(?, ''), localidad),
            fecha_fallecimiento = COALESCE(NULLIF(?, ''), fecha_fallecimiento),
            fecha_nacimiento = COALESCE(NULLIF(?, ''), fecha_nacimiento),
            lugar_nacimiento = COALESCE(NULLIF(?, ''), lugar_nacimiento),
            lugar_fallecimiento = COALESCE(NULLIF(?, ''), lugar_fallecimiento),
            referencia_catastral = COALESCE(NULLIF(?, ''), referencia_catastral),
            ref_catastral = COALESCE(NULLIF(?, ''), ref_catastral),
            m2 = COALESCE(?, m2),
            year_built = COALESCE(?, year_built),
            use_class = COALESCE(NULLIF(?, ''), use_class),
            neighborhood = COALESCE(NULLIF(?, ''), neighborhood),
            juzgado = COALESCE(NULLIF(?, ''), juzgado),
            sources = ?,
            source_urls = ?,
            last_updated_at = datetime('now')
        WHERE id = ?""",
        (
            record.causante or "",
            normalize_name(record.causante),
            best_address,
            normalize_address(best_address),
            getattr(record, "localidad", None) or "",
            getattr(record, "fecha_fallecimiento", None) or "",
            getattr(record, "fecha_nacimiento", None) or "",
            getattr(record, "lugar_nacimiento", None) or "",
            getattr(record, "lugar_fallecimiento", None) or "",
            record.referencia_catastral or "",
            record.referencia_catastral or "",
            parcel.get("m2"),
            parcel.get("year_built"),
            parcel.get("use_class") or "",
            parcel.get("neighborhood") or "",
            getattr(record, "juzgado", None) or "",
            json.dumps(sources),
            json.dumps(source_urls),
            lead_id,
        ),
    )


def _create_new_lead(
    conn: sqlite3.Connection,
    record: EdictRecord,
    parcel: dict,
    best_address: str,
    source_label: str,
) -> int:
    """Create a new lead from a record. Returns the new lead_id."""
    subsource = SUBSOURCE_CODES.get(record.source, record.source)
    cursor = conn.execute(
        """INSERT INTO leads (
            causante_norm, ref_catastral, address_norm,
            causante, direccion, localidad,
            fecha_fallecimiento, fecha_nacimiento,
            lugar_nacimiento, lugar_fallecimiento,
            referencia_catastral, m2, year_built, use_class, neighborhood,
            sources, source_urls, subsource, juzgado
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            normalize_name(record.causante),
            record.referencia_catastral,
            normalize_address(best_address),
            record.causante or "",
            best_address,
            getattr(record, "localidad", None) or "",
            getattr(record, "fecha_fallecimiento", None) or "",
            getattr(record, "fecha_nacimiento", None) or "",
            getattr(record, "lugar_nacimiento", None) or "",
            getattr(record, "lugar_fallecimiento", None) or "",
            record.referencia_catastral or "",
            parcel.get("m2"),
            parcel.get("year_built"),
            parcel.get("use_class") or "",
            parcel.get("neighborhood") or "",
            json.dumps([source_label]),
            json.dumps([record.source_url] if record.source_url else []),
            subsource,
            getattr(record, "juzgado", None) or "",
        ),
    )
    return cursor.lastrowid


def get_todays_leads(conn: sqlite3.Connection) -> list[dict]:
    """Get leads first seen today, sorted by tier then m² descending."""
    rows = conn.execute(
        """SELECT * FROM leads
        WHERE date(first_seen_at) = date('now')
        ORDER BY
            CASE tier WHEN 'A' THEN 0 WHEN 'B' THEN 1 WHEN 'C' THEN 2 WHEN 'X' THEN 3 END,
            m2 DESC NULLS LAST"""
    ).fetchall()
    return [dict(row) for row in rows]


def get_all_leads(conn: sqlite3.Connection) -> list[dict]:
    """Get all leads (for full export), sorted by tier then date."""
    rows = conn.execute(
        """SELECT * FROM leads
        ORDER BY
            CASE tier WHEN 'A' THEN 0 WHEN 'B' THEN 1 WHEN 'C' THEN 2 WHEN 'X' THEN 3 END,
            first_seen_at DESC,
            m2 DESC NULLS LAST"""
    ).fetchall()
    return [dict(row) for row in rows]
