"""Multi-tenant isolation — one deployment, many agents, zero data bleed.

Each "tenant" is a real-estate agent (or agency) with their own territory
(a list of provinces and/or specific localidades).  The `leads` table is
shared, but every query that touches the worklist is funnelled through
``leads_for_tenant``, which hard-gates on the tenant's territory.

Matching strategy (broadest → narrowest — an OR, never an AND):
  1. ``region`` — the province rollup already written by merge.py /
     ``province_for_localidad``; covers all towns whose province is in the
     territory list.  Case-insensitive accent-stripped comparison.
  2. ``localidad`` — exact-ish match after stripping accents and lower-casing;
     covers cases where ``region`` is blank (sparse leads) or where the tenant
     specified individual towns rather than whole provinces.

Both use the same ``strip_accents`` helper already in merge.py — no new
normalisation logic introduced here.

Province resolution deliberately delegates to ``merge.province_for_localidad``
so that the town→province map lives in exactly one place (merge.py) and
tenancy stays in sync automatically when that map grows.

Schema
------
tenants
  id              INTEGER PK AUTOINCREMENT
  name            TEXT NOT NULL          -- agent's display name
  agency          TEXT NOT NULL DEFAULT ''
  phone           TEXT NOT NULL DEFAULT ''
  territory_json  TEXT NOT NULL DEFAULT '[]'   -- JSON list[str] of province/loc names
  active          INTEGER NOT NULL DEFAULT 1   -- 0 = soft-disabled
  created_at      TEXT NOT NULL               -- ISO-8601 UTC
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime

from nadia_ai.merge import strip_accents

logger = logging.getLogger("nadia_ai.tenancy")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tenants (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    agency          TEXT NOT NULL DEFAULT '',
    phone           TEXT NOT NULL DEFAULT '',
    territory_json  TEXT NOT NULL DEFAULT '[]',
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL
);
"""

_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_tenants_name ON tenants(name);
CREATE INDEX IF NOT EXISTS idx_tenants_active ON tenants(active);
"""


# ── Schema ────────────────────────────────────────────────────────────────────


def init_tenancy_schema(conn: sqlite3.Connection) -> None:
    """Create the tenants table + indexes (idempotent)."""
    conn.executescript(_SCHEMA_SQL + _INDEX_SQL)
    conn.commit()


# ── Normalisation ─────────────────────────────────────────────────────────────


def _norm(s: str | None) -> str:
    """Accent-strip + lowercase for territory comparisons."""
    if not s:
        return ""
    return strip_accents(s).lower().strip()


def _territory_norms(territory: list[str]) -> set[str]:
    """Return the set of normalised territory tokens for fast membership tests."""
    return {_norm(t) for t in territory if t}


# ── CRUD ──────────────────────────────────────────────────────────────────────


def add_tenant(
    conn: sqlite3.Connection,
    *,
    name: str,
    agency: str = "",
    phone: str = "",
    territory: list[str] | None = None,
    active: bool = True,
) -> int:
    """Insert a new tenant row.  Returns the new tenant id.

    ``territory`` is a list of province names and/or specific localidades —
    e.g. ``["Zaragoza", "Huesca", "Calatayud"]``.  An empty list means the
    tenant is not yet scoped to any territory (no leads will be returned).
    """
    if not name or not name.strip():
        raise ValueError("tenant name cannot be empty")
    territory = territory or []
    init_tenancy_schema(conn)
    cur = conn.execute(
        """INSERT INTO tenants (name, agency, phone, territory_json, active, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            name.strip(),
            (agency or "").strip(),
            (phone or "").strip(),
            json.dumps(territory),
            int(active),
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()
    logger.info("Tenant added: %r (id=%d territory=%s)", name, cur.lastrowid, territory)
    return cur.lastrowid


def get_tenant(conn: sqlite3.Connection, tenant_id: int) -> dict | None:
    """Fetch a single tenant by id.  Returns a dict or None if not found."""
    init_tenancy_schema(conn)
    row = conn.execute(
        "SELECT id, name, agency, phone, territory_json, active, created_at "
        "FROM tenants WHERE id = ?",
        (tenant_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def list_tenants(conn: sqlite3.Connection, *, active_only: bool = False) -> list[dict]:
    """Return all tenants, newest first.

    Pass ``active_only=True`` to skip disabled tenants.
    """
    init_tenancy_schema(conn)
    sql = (
        "SELECT id, name, agency, phone, territory_json, active, created_at "
        "FROM tenants"
    )
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY created_at DESC"
    rows = conn.execute(sql).fetchall()
    return [_row_to_dict(r) for r in rows]


def set_tenant_active(conn: sqlite3.Connection, tenant_id: int, active: bool) -> bool:
    """Enable or disable a tenant.  Returns True if the row existed."""
    init_tenancy_schema(conn)
    cur = conn.execute(
        "UPDATE tenants SET active = ? WHERE id = ?",
        (int(active), tenant_id),
    )
    conn.commit()
    return cur.rowcount > 0


def update_territory(
    conn: sqlite3.Connection, tenant_id: int, territory: list[str]
) -> bool:
    """Replace a tenant's territory list.  Returns True if the row existed."""
    init_tenancy_schema(conn)
    cur = conn.execute(
        "UPDATE tenants SET territory_json = ? WHERE id = ?",
        (json.dumps(territory), tenant_id),
    )
    conn.commit()
    return cur.rowcount > 0


# ── Lead scoping ──────────────────────────────────────────────────────────────


def leads_for_tenant(
    conn: sqlite3.Connection,
    tenant: dict,
    *,
    order_sql: str = (
        "CASE tier WHEN 'A' THEN 0 WHEN 'B' THEN 1 WHEN 'C' THEN 2 WHEN 'X' THEN 3 END,"
        "CASE WHEN edict_window_days IS NOT NULL AND edict_window_days > 0 THEN 0 ELSE 1 END,"
        "CASE WHEN edict_window_days IS NOT NULL AND edict_window_days > 0"
        "     THEN edict_window_days ELSE 999999 END ASC,"
        "days_since_death DESC NULLS LAST,"
        "first_seen_at DESC"
    ),
    extra_where: str = "",
    params: tuple = (),
) -> list[dict]:
    """Return only the leads that fall within the tenant's territory.

    Matching uses two complementary columns:

    ``region``    — province-level rollup set by ``merge.province_for_localidad``
                    at write time (e.g. "Zaragoza" for Calatayud, Ejea …).
    ``localidad`` — raw city name, accent-stripped at query time; catches leads
                    whose ``region`` column was not set or where the tenant
                    scoped to an individual town rather than a whole province.

    Both comparisons are accent-stripped so "Zaragoza" == "zaragoza" == "Záragoza".

    If the tenant's ``territory_json`` is empty, an empty list is returned
    immediately — avoids a vacuously-true WHERE clause that would return all leads.

    ``extra_where`` and ``params`` let callers layer additional SQL predicates
    (e.g. ``outreach_allowed = 1``, or ``date(first_seen_at) = date('now')``).
    The string must start with "AND …" and must not include the leading WHERE.
    """
    territory: list[str] = tenant.get("territory_json", [])
    if isinstance(territory, str):
        try:
            territory = json.loads(territory)
        except json.JSONDecodeError:
            territory = []

    if not territory:
        logger.debug("Tenant %r has empty territory — returning no leads", tenant.get("name"))
        return []

    norms = _territory_norms(territory)

    # Fetch all leads (with the optional extra filter) and apply territory in
    # Python rather than SQL.  SQLite has no accent-stripping function, and we
    # do NOT want to add a custom Python UDF to every connection — that would
    # couple tenancy to the connection lifecycle.  The leads table is typically
    # ≤10k rows, so loading them all is fast.
    where_parts = ["1=1"]
    all_params: list = list(params)
    if extra_where:
        # Caller passes "AND col = ?" style; strip leading AND/WHERE for safety.
        cleaned = extra_where.strip()
        if cleaned.upper().startswith("AND "):
            cleaned = cleaned[4:]
        where_parts.append(cleaned)

    sql = f"SELECT * FROM leads WHERE {' AND '.join(where_parts)} ORDER BY {order_sql}"
    rows = conn.execute(sql, all_params).fetchall()

    # Convert to dicts (works whether conn.row_factory is sqlite3.Row or None)
    leads: list[dict] = []
    for r in rows:
        d = dict(r) if hasattr(r, "keys") else dict(zip([c[0] for c in conn.execute("PRAGMA table_info(leads)")], r))
        leads.append(d)

    # Territory filter: include lead if region OR localidad matches any territory token.
    filtered = [l for l in leads if _lead_in_territory(l, norms)]
    logger.debug(
        "Tenant %r territory=%s: %d/%d leads matched",
        tenant.get("name"), territory, len(filtered), len(leads),
    )
    return filtered


def _lead_in_territory(lead: dict, norms: set[str]) -> bool:
    """True if this lead's region or localidad normalises to any territory token."""
    region_n = _norm(lead.get("region") or "")
    if region_n and region_n in norms:
        return True
    loc_n = _norm(lead.get("localidad") or "")
    if loc_n and loc_n in norms:
        return True
    return False


# ── Helpers ───────────────────────────────────────────────────────────────────


def _row_to_dict(row) -> dict:
    """Convert a DB row to a plain dict, deserialising territory_json."""
    d = dict(row) if hasattr(row, "keys") else {
        "id": row[0], "name": row[1], "agency": row[2],
        "phone": row[3], "territory_json": row[4],
        "active": row[5], "created_at": row[6],
    }
    # Deserialise territory so callers always get a list, never a raw JSON string.
    raw = d.get("territory_json", "[]")
    try:
        d["territory_json"] = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        d["territory_json"] = []
    return d
