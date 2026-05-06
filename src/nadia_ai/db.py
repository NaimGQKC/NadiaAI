"""SQLite database setup and operations."""

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nadia_ai.config import DB_PATH, PERSON_TTL_DAYS

logger = logging.getLogger("nadia_ai.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS parcels (
    referencia_catastral TEXT PRIMARY KEY,
    address TEXT,
    neighborhood TEXT,
    m2 REAL,
    year_built INTEGER,
    use_class TEXT,
    enrichment_pending INTEGER DEFAULT 0,
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS edicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    referencia_catastral TEXT,
    edict_type TEXT,
    published_at TEXT,
    source_url TEXT,
    address TEXT,
    ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source, source_id),
    FOREIGN KEY (referencia_catastral) REFERENCES parcels(referencia_catastral)
);

CREATE TABLE IF NOT EXISTS edict_persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    edict_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    full_name TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (edict_id) REFERENCES edicts(id)
);

CREATE INDEX IF NOT EXISTS idx_edicts_source ON edicts(source, source_id);
CREATE INDEX IF NOT EXISTS idx_edicts_rc ON edicts(referencia_catastral);
CREATE INDEX IF NOT EXISTS idx_edict_persons_expires ON edict_persons(expires_at);
"""


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Get a SQLite connection with WAL mode and foreign keys enabled."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables and indexes if they don't exist."""
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    logger.info("Database schema initialized")


def purge_expired_persons(conn: sqlite3.Connection) -> int:
    """Delete person records past their TTL. Returns count deleted."""
    now = datetime.now(UTC).isoformat()
    cursor = conn.execute("DELETE FROM edict_persons WHERE expires_at < ?", (now,))
    count = cursor.rowcount
    conn.commit()
    if count > 0:
        logger.info("Purged %d expired person records", count)
    return count


def person_expiry_date() -> str:
    """Calculate the expiry date for a new person record."""
    return (datetime.now(UTC) + timedelta(days=PERSON_TTL_DAYS)).isoformat()


def upsert_parcel(conn: sqlite3.Connection, data: dict) -> None:
    """Insert or update a parcel record."""
    conn.execute(
        """
        INSERT INTO parcels (referencia_catastral, address, neighborhood, m2,
                             year_built, use_class, enrichment_pending)
        VALUES (:referencia_catastral, :address, :neighborhood, :m2,
                :year_built, :use_class, :enrichment_pending)
        ON CONFLICT(referencia_catastral) DO UPDATE SET
            address = COALESCE(excluded.address, parcels.address),
            neighborhood = COALESCE(excluded.neighborhood, parcels.neighborhood),
            m2 = COALESCE(excluded.m2, parcels.m2),
            year_built = COALESCE(excluded.year_built, parcels.year_built),
            use_class = COALESCE(excluded.use_class, parcels.use_class),
            enrichment_pending = excluded.enrichment_pending,
            last_updated_at = datetime('now')
        """,
        data,
    )
    conn.commit()


def insert_edict(conn: sqlite3.Connection, data: dict) -> int | None:
    """Insert an edict record. Returns the row id, or None if duplicate."""
    try:
        cursor = conn.execute(
            """
            INSERT INTO edicts (source, source_id, referencia_catastral,
                                edict_type, published_at, source_url, address)
            VALUES (:source, :source_id, :referencia_catastral,
                    :edict_type, :published_at, :source_url, :address)
            """,
            data,
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        # Duplicate (source, source_id) — skip silently
        return None


def insert_person(conn: sqlite3.Connection, edict_id: int, role: str, full_name: str) -> None:
    """Insert a person record with TTL expiry."""
    conn.execute(
        """
        INSERT INTO edict_persons (edict_id, role, full_name, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (edict_id, role, full_name, person_expiry_date()),
    )
    conn.commit()


def get_existing_ref_catastrales(conn: sqlite3.Connection) -> set[str]:
    """Get all referencia_catastral values already in the leads sheet."""
    rows = conn.execute("SELECT referencia_catastral FROM parcels").fetchall()
    return {row["referencia_catastral"] for row in rows}


def get_todays_leads(conn: sqlite3.Connection) -> list[dict]:
    """Get leads ingested in the last 24 hours, sorted by m² descending."""
    rows = conn.execute(
        """
        SELECT e.source, e.referencia_catastral, e.edict_type, e.published_at,
               e.source_url, e.ingested_at,
               COALESCE(p.address, e.address) as address,
               p.neighborhood, p.m2, p.year_built, p.use_class,
               p.enrichment_pending,
               (SELECT full_name FROM edict_persons ep
                WHERE ep.edict_id = e.id AND ep.role = 'causante'
                LIMIT 1) as causante
        FROM edicts e
        LEFT JOIN parcels p ON e.referencia_catastral = p.referencia_catastral
        WHERE e.ingested_at >= datetime('now', '-1 day')
        ORDER BY
            CASE WHEN p.use_class IN ('Residencial', 'Vivienda') THEN 0 ELSE 1 END,
            p.m2 DESC NULLS LAST
        """
    ).fetchall()
    return [dict(row) for row in rows]
