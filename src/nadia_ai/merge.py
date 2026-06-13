"""Lead deduplication, merging, tier classification, and Plusvalía Clock.

After scrapers run and edicts are persisted, this module:
1. Matches new records against existing leads (by RC, name, or address)
2. Merges data from multiple sources into a single lead
3. Classifies each lead into tiers (A/B/C/X) with urgency phases
4. Computes the Plusvalía Clock (days since death, tax deadline)
5. Sets outreach-legality flags
"""

import json
import logging
import re
import sqlite3
import unicodedata
from datetime import UTC, datetime

from nadia_ai.models import EdictRecord
from nadia_ai.utils.names import is_valid_person_name

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
    -- Plusvalía Clock
    date_of_death TEXT DEFAULT '',
    days_since_death INTEGER,
    urgency_phase TEXT DEFAULT '',
    tax_deadline TEXT DEFAULT '',
    -- Heir data (Phase 2)
    heir_names_json TEXT NOT NULL DEFAULT '[]',
    heir_name TEXT DEFAULT '',
    social_profile_url TEXT DEFAULT '',
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
    -- Kanban / agent workflow
    kanban_status TEXT DEFAULT 'new_to_call',
    estado TEXT DEFAULT 'Nuevo',
    notas TEXT DEFAULT '',
    region TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS lead_edicts (
    lead_id INTEGER NOT NULL,
    edict_id INTEGER NOT NULL,
    PRIMARY KEY (lead_id, edict_id),
    FOREIGN KEY (lead_id) REFERENCES leads(id),
    FOREIGN KEY (edict_id) REFERENCES edicts(id)
);
"""

LEADS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_leads_causante_norm ON leads(causante_norm);
CREATE INDEX IF NOT EXISTS idx_leads_ref_catastral ON leads(ref_catastral);
CREATE INDEX IF NOT EXISTS idx_leads_address_norm ON leads(address_norm);
CREATE INDEX IF NOT EXISTS idx_leads_first_seen ON leads(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_leads_urgency ON leads(urgency_phase);
"""

# Only Death & Heirs sources — Solares, Licencias, Servihabitat deleted
SOURCE_LABELS = {
    "tablon": "Tablón",
    "boa": "BOA",
    "bop": "BOP Zaragoza",
    "boe_teju": "BOE (TEJU)",
    "boe_secv": "BOE (Sec.V)",
    "boe_n": "BOE Notarial",
    "boe_nationwide": "BOE",
    "rememori": "Rememori",
    "borme_i": "BORME-I",
    "borme_ii": "BORME-II",
    "subastas": "Subastas BOE",
    "esquelas": "Esquelas",
    "defunciones": "Defunciones",
    "iesquelas": "iEsquelas",
    "cee": "CEE Aragón",
    "traspasos": "Traspasos Aragón",
    "ite": "ITE Zaragoza",
}

SUBSOURCE_CODES = {
    "tablon": "Tablón",
    "boa": "BOA-JD",
    "bop": "BOPZ",
    "boe_teju": "BOE-TEJU",
    "boe_secv": "BOE-V.B",
    "boe_n": "BOE-N",
    "boe_nationwide": "BOE",
    "rememori": "Rememori",
    "borme_i": "BORME-I",
    "borme_ii": "BORME-II",
    "esquelas": "Esquelas",
    "defunciones": "Defunciones",
    "iesquelas": "iEsquelas",
    "cee": "CEE",
    "traspasos": "Traspasos",
    "ite": "ITE",
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


# ── Plusvalía Clock ────────────────────────────────────────────────


def compute_days_since_death(date_of_death_str: str | None) -> int | None:
    """Compute days elapsed since date of death. Returns None if no date."""
    if not date_of_death_str:
        return None
    try:
        dod = datetime.fromisoformat(date_of_death_str)
        if dod.tzinfo is None:
            dod = dod.replace(tzinfo=UTC)
        delta = (datetime.now(UTC) - dod).days
        return max(0, delta)
    except (ValueError, TypeError):
        return None


def compute_urgency_phase(days: int | None) -> str:
    """Map days since death to urgency phase.

    0-90:   Respectful/Monitoring — too early for aggressive outreach
    91-150: High Motivation — tax deadline approaching, heirs feeling pressure
    150+:   Urgent/Distress — penalties starting soon, highest motivation to sell
    """
    if days is None:
        return ""
    if days <= 90:
        return "monitoring"
    elif days <= 150:
        return "high_motivation"
    return "urgent_distress"


def compute_tax_deadline(date_of_death_str: str | None) -> str:
    """Compute the 180-day Plusvalía tax deadline as ISO date string."""
    if not date_of_death_str:
        return ""
    try:
        from datetime import timedelta
        dod = datetime.fromisoformat(date_of_death_str)
        deadline = dod + timedelta(days=180)
        return deadline.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return ""


def recompute_all_deadlines(conn: sqlite3.Connection) -> int:
    """Recompute days_since_death and urgency_phase for ALL leads.

    Called at the start of every pipeline run to keep urgency current.
    Returns count of leads updated.
    """
    rows = conn.execute(
        "SELECT id, date_of_death FROM leads WHERE date_of_death IS NOT NULL AND date_of_death != ''"
    ).fetchall()
    updated = 0
    for row in rows:
        days = compute_days_since_death(row["date_of_death"])
        phase = compute_urgency_phase(days)
        conn.execute(
            "UPDATE leads SET days_since_death = ?, urgency_phase = ? WHERE id = ?",
            (days, phase, row["id"]),
        )
        updated += 1
    conn.commit()
    if updated:
        logger.info("Recomputed deadlines for %d leads", updated)
    return updated


# ── Tier and outreach classification ───────────────────────────────


def compute_tier(lead: dict) -> str:
    """Compute tier classification for a lead.

    Tier A (Hot):  Name + Address + fresh signal (< 3 months since death)
    Tier B (Warm): Address found but data incomplete, OR 91-150 days since death
    Tier C (Cold): Signal detected but insufficient data to act
    Tier X (Distress): Properties in judicial auction phase
    """
    sources = json.loads(lead.get("sources", "[]"))
    is_distress = any(
        "subasta" in s.lower() or "concurso" in s.lower() for s in sources
    )
    if is_distress:
        return "X"

    has_name = bool(lead.get("causante"))
    has_address = bool(lead.get("direccion"))
    days = lead.get("days_since_death")

    # ITE desfavorable is a capex signal — outreach allowed but Tier B
    is_ite = any("ite" in s.lower() for s in sources)
    if is_ite and has_address:
        return "B"

    # Fresh signal with full data = Tier A
    if has_name and has_address:
        if days is not None and days <= 90:
            return "A"
        elif days is not None and days <= 150:
            return "A"  # Still actionable during high motivation phase
        elif days is not None and days > 180:
            return "B"  # Past deadline, lower priority
        return "A"  # No death date but has name+address = A

    if has_name or has_address:
        # Auto-escalate if nearing tax deadline
        if days is not None and 91 <= days <= 180:
            return "B"
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

    if any("traspaso" in s.lower() for s in sources):
        return True, "Contexto B2B — traspaso de negocio"

    if any("ite" in s.lower() for s in sources):
        return True, "ITE desfavorable — posible gasto capital obligatorio"

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
    # Phase 2: Plusvalía Clock + heir data
    ("date_of_death", "ALTER TABLE leads ADD COLUMN date_of_death TEXT DEFAULT ''"),
    ("days_since_death", "ALTER TABLE leads ADD COLUMN days_since_death INTEGER"),
    ("urgency_phase", "ALTER TABLE leads ADD COLUMN urgency_phase TEXT DEFAULT ''"),
    ("tax_deadline", "ALTER TABLE leads ADD COLUMN tax_deadline TEXT DEFAULT ''"),
    ("heir_names_json", "ALTER TABLE leads ADD COLUMN heir_names_json TEXT NOT NULL DEFAULT '[]'"),
    ("heir_name", "ALTER TABLE leads ADD COLUMN heir_name TEXT DEFAULT ''"),
    ("social_profile_url", "ALTER TABLE leads ADD COLUMN social_profile_url TEXT DEFAULT ''"),
    ("kanban_status", "ALTER TABLE leads ADD COLUMN kanban_status TEXT DEFAULT 'new_to_call'"),
    ("region", "ALTER TABLE leads ADD COLUMN region TEXT DEFAULT ''"),
    ("ai_extraction_done", "ALTER TABLE leads ADD COLUMN ai_extraction_done INTEGER DEFAULT 0"),
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
    conn.executescript(LEADS_INDEX_SQL)
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

    # Phase 2: match by source URL (prevents duplicates for unnamed candidates)
    if record.source_url:
        # source_urls is a JSON array in the DB
        row = conn.execute(
            "SELECT id FROM leads WHERE source_urls LIKE ? LIMIT 1",
            (f'%"{record.source_url}"%',),
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


def _resolve_date_of_death(record: EdictRecord) -> str:
    """Extract and standardize date of death from multiple sources.

    Priority: date_of_death > fecha_fallecimiento > published_at (for obituary sources).
    Obituaries are published within days of death, so publish date ≈ death date.
    """
    if record.date_of_death:
        return record.date_of_death
    if record.fecha_fallecimiento:
        return record.fecha_fallecimiento
    # For obituary sources, published_at IS roughly the death date.
    # "rememori" parses a real publication date from the page; include it here
    # alongside the other obituary aggregators.
    if record.source in ("esquelas", "defunciones", "iesquelas", "rememori") and record.published_at:
        return record.published_at.strftime("%Y-%m-%d")
    return ""


# Sources that legitimately use a business/entity name instead of a person name.
# is_valid_person_name would reject them, but we must not null them out.
_B2B_SOURCES = {"traspasos", "borme_i", "borme_ii"}


def _sanitize_causante(record: EdictRecord) -> str | None:
    """Return a cleaned causante value, or None if it is junk.

    For obituary and legal sources (non-B2B), a causante that fails
    person-name validation is dropped rather than stored.  This prevents
    single-letter fragments ("D"), honorific prefixes, and title-cased
    boilerplate from polluting the leads table.

    B2B sources (traspasos, borme) are exempt — their causante is a
    company/entity name, which legitimately fails person-name checks.
    """
    causante = record.causante
    if not causante:
        return None
    if record.source in _B2B_SOURCES:
        # B2B entity names — keep as-is regardless of validation
        return causante
    if is_valid_person_name(causante):
        return causante
    # Junk for a person-name source — drop it
    logger.debug(
        "Dropping invalid causante %r from source %r", causante, record.source
    )
    return None


def _update_lead_classification(conn: sqlite3.Connection, lead_id: int) -> None:
    """Recompute tier, outreach, and Plusvalía Clock for a lead."""
    lead = dict(conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone())

    # Plusvalía Clock
    dod = lead.get("date_of_death") or ""
    days = compute_days_since_death(dod)
    phase = compute_urgency_phase(days)
    deadline = compute_tax_deadline(dod)

    # Tier and outreach
    lead["days_since_death"] = days  # inject for compute_tier
    tier = compute_tier(lead)
    ok, notes = compute_outreach(lead)

    conn.execute(
        """UPDATE leads SET
            tier = ?, outreach_allowed = ?, outreach_notes = ?,
            days_since_death = ?, urgency_phase = ?, tax_deadline = ?
        WHERE id = ?""",
        (tier, int(ok), notes, days, phase, deadline, lead_id),
    )


# ── Main merge entry point ─────────────────────────────────────────


def merge_leads(conn: sqlite3.Connection, records: list[EdictRecord]) -> dict:
    """Merge scraped records into the leads table.

    For each record:
    1. Skip if no name AND no address (noise)
    2. Find matching lead by RC → name → address
    3. If found: merge data (COALESCE, add source)
    4. If not found: create new lead
    5. Classify tier, outreach, and Plusvalía Clock
    6. Link edict to lead

    Returns stats dict: {created, merged, skipped}.
    """
    init_leads_schema(conn)
    stats = {"created": 0, "merged": 0, "skipped": 0}

    for record in records:
        # Phase 2: Allow records without name/address if they have a URL (for AI extraction)
        if not record.causante and not record.address and not record.source_url:
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

    # Merge heir names
    existing_heirs = json.loads(existing.get("heir_names_json") or "[]")
    new_heirs = list(set(existing_heirs + record.heir_names))
    heir_name = existing.get("heir_name") or (new_heirs[0] if new_heirs else "")

    # Resolve best date of death
    dod = _resolve_date_of_death(record) or existing.get("date_of_death") or ""

    # Validate causante: drop junk values from person-name sources
    clean_causante = _sanitize_causante(record) or ""

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
            date_of_death = COALESCE(NULLIF(?, ''), date_of_death),
            heir_names_json = ?,
            heir_name = COALESCE(NULLIF(?, ''), heir_name),
            region = COALESCE(NULLIF(?, ''), region),
            sources = ?,
            source_urls = ?,
            last_updated_at = datetime('now')
        WHERE id = ?""",
        (
            clean_causante,
            normalize_name(clean_causante) if clean_causante else None,
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
            dod,
            json.dumps(new_heirs),
            heir_name,
            getattr(record, "localidad", None) or "",
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
    dod = _resolve_date_of_death(record)
    days = compute_days_since_death(dod)
    phase = compute_urgency_phase(days)
    deadline = compute_tax_deadline(dod)
    heir_name = record.heir_names[0] if record.heir_names else ""

    # Validate causante: drop junk values from person-name sources
    clean_causante = _sanitize_causante(record) or ""

    cursor = conn.execute(
        """INSERT INTO leads (
            causante_norm, ref_catastral, address_norm,
            causante, direccion, localidad,
            fecha_fallecimiento, fecha_nacimiento,
            lugar_nacimiento, lugar_fallecimiento,
            referencia_catastral, m2, year_built, use_class, neighborhood,
            sources, source_urls, subsource, juzgado,
            date_of_death, days_since_death, urgency_phase, tax_deadline,
            heir_names_json, heir_name, region
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            normalize_name(clean_causante) if clean_causante else None,
            record.referencia_catastral,
            normalize_address(best_address),
            clean_causante,
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
            dod,
            days,
            phase,
            deadline,
            json.dumps(record.heir_names),
            heir_name,
            getattr(record, "localidad", None) or "",
        ),
    )
    return cursor.lastrowid


def get_todays_leads(conn: sqlite3.Connection) -> list[dict]:
    """Get leads first seen today, sorted by tier then urgency."""
    rows = conn.execute(
        """SELECT * FROM leads
        WHERE date(first_seen_at) = date('now')
        ORDER BY
            CASE tier WHEN 'A' THEN 0 WHEN 'B' THEN 1 WHEN 'C' THEN 2 WHEN 'X' THEN 3 END,
            days_since_death DESC NULLS LAST,
            m2 DESC NULLS LAST"""
    ).fetchall()
    return [dict(row) for row in rows]


def get_all_leads(conn: sqlite3.Connection) -> list[dict]:
    """Get all leads (for full export), sorted by tier then urgency."""
    rows = conn.execute(
        """SELECT * FROM leads
        ORDER BY
            CASE tier WHEN 'A' THEN 0 WHEN 'B' THEN 1 WHEN 'C' THEN 2 WHEN 'X' THEN 3 END,
            days_since_death DESC NULLS LAST,
            first_seen_at DESC,
            m2 DESC NULLS LAST"""
    ).fetchall()
    return [dict(row) for row in rows]
