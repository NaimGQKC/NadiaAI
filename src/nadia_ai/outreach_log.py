"""Outreach logging, outcome tracking, and conversion funnel.

Every lead that reaches a worklist should be recorded here the moment
the agent contacts (or attempts to contact) them.  The log acts as the
do-not-double-contact guard and the single source of truth for funnel
analytics (contacted → responded → interested → listed).

Outcomes follow a simple state machine::

    pending → no_answer | not_interested | interested | opted_out
    interested → listed

If a lead is marked ``opted_out``, the person is also written into
``suppression_list`` via :func:`nadia_ai.suppression.add_suppression` so
they can never resurface in a future worklist.

Schema note
-----------
The table ``outreach_log`` already exists in the DB with columns::

    id, lead_id, action, channel, legal_basis, notes, created_at

This module extends it (via ``ALTER TABLE … ADD COLUMN IF NOT EXISTS``
approach) with three extra columns::

    tipo       TEXT NOT NULL DEFAULT ''
    outcome    TEXT NOT NULL DEFAULT 'pending'
    outcome_at TEXT                          (NULL until recorded)

``sent_at`` is served by the existing ``created_at`` column so we keep
a stable alias via the ``sent_at`` alias in SELECTs.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta

logger = logging.getLogger("nadia_ai.outreach_log")

VALID_OUTCOMES = frozenset(
    {"pending", "no_answer", "not_interested", "interested", "listed", "opted_out"}
)

_EXTRA_COLS: list[tuple[str, str]] = [
    ("tipo", "TEXT NOT NULL DEFAULT ''"),
    ("outcome", "TEXT NOT NULL DEFAULT 'pending'"),
    ("outcome_at", "TEXT"),
]


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def _col_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)


def init_outreach_log_schema(conn: sqlite3.Connection) -> None:
    """Ensure the outreach_log table exists and has all required columns.

    Called idempotently — safe to run on every startup.  The base table is
    created by :func:`nadia_ai.db.init_db`; this function only adds the
    extra columns we need if they are missing.
    """
    # Create the table in case the caller is using a fresh in-memory DB that
    # skipped init_db (e.g. unit tests that only import this module).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS outreach_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id     INTEGER NOT NULL,
            action      TEXT NOT NULL DEFAULT 'outreach',
            channel     TEXT NOT NULL DEFAULT '',
            legal_basis TEXT NOT NULL DEFAULT '',
            notes       TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            tipo        TEXT NOT NULL DEFAULT '',
            outcome     TEXT NOT NULL DEFAULT 'pending',
            outcome_at  TEXT
        )
        """
    )
    # Migrate existing tables that are missing the extended columns.
    for col_name, col_def in _EXTRA_COLS:
        if not _col_exists(conn, "outreach_log", col_name):
            conn.execute(
                f"ALTER TABLE outreach_log ADD COLUMN {col_name} {col_def}"
            )
            logger.info("outreach_log: added column %s", col_name)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_outreach_log_lead "
        "ON outreach_log(lead_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_outreach_log_outcome "
        "ON outreach_log(outcome)"
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def log_outreach(
    conn: sqlite3.Connection,
    lead_id: int,
    channel: str,
    tipo: str,
    *,
    cooldown_days: int = 30,
    notes: str = "",
    legal_basis: str = "6.1.f RGPD",
) -> bool:
    """Record an outreach attempt for a lead.

    Idempotent within the cooldown window: if this ``(lead_id, channel)``
    pair already has a log entry in the last ``cooldown_days`` days the
    function is a no-op and returns ``False``.  Otherwise it inserts a new
    row (outcome = 'pending') and returns ``True``.

    Parameters
    ----------
    conn:
        Open SQLite connection (write access required).
    lead_id:
        The ``leads.id`` of the lead being contacted.
    channel:
        Delivery channel: ``'call'``, ``'whatsapp'``, or ``'letter'``.
    tipo:
        Lead type as rendered by ``nadia_ai.outreach.lead_type`` (e.g.
        ``'FSBO'``, ``'BOE-N'``, ``'Heraldo'``).
    cooldown_days:
        How many days must pass before re-contacting the same lead on the
        same channel.  Default 30.
    notes:
        Optional free-text note attached to the log row.
    legal_basis:
        RGPD legal basis (defaults to the legitimate-interest ground used
        across the pipeline).

    Returns
    -------
    bool
        ``True`` if a new row was written; ``False`` if skipped (duplicate
        within window).
    """
    init_outreach_log_schema(conn)

    cutoff = (datetime.now(UTC) - timedelta(days=cooldown_days)).isoformat()
    existing = conn.execute(
        """
        SELECT id FROM outreach_log
        WHERE lead_id = ? AND channel = ? AND created_at >= ?
        LIMIT 1
        """,
        (lead_id, channel, cutoff),
    ).fetchone()

    if existing:
        logger.debug(
            "log_outreach: lead %s / channel %s already logged within %d days — skipped",
            lead_id,
            channel,
            cooldown_days,
        )
        return False

    conn.execute(
        """
        INSERT INTO outreach_log
            (lead_id, action, channel, tipo, legal_basis, notes,
             created_at, outcome, outcome_at)
        VALUES (?, 'outreach', ?, ?, ?, ?, ?, 'pending', NULL)
        """,
        (
            lead_id,
            channel,
            tipo,
            legal_basis,
            notes,
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()
    logger.info(
        "Outreach logged: lead_id=%s channel=%s tipo=%s", lead_id, channel, tipo
    )
    return True


def record_outcome(
    conn: sqlite3.Connection,
    lead_id: int,
    outcome: str,
    notes: str = "",
) -> bool:
    """Update the most recent outreach row for this lead with an outcome.

    If ``outcome == 'opted_out'`` the lead's contact details are also
    written to ``suppression_list`` (via
    :func:`nadia_ai.suppression.add_suppression`) so they can never
    reappear in a future worklist.

    Parameters
    ----------
    conn:
        Open SQLite connection.
    lead_id:
        The lead whose most-recent outreach row should be updated.
    outcome:
        One of: ``pending``, ``no_answer``, ``not_interested``,
        ``interested``, ``listed``, ``opted_out``.
    notes:
        Optional free-text note appended to the existing notes.

    Returns
    -------
    bool
        ``True`` if a row was updated, ``False`` if no outreach row exists.

    Raises
    ------
    ValueError
        If *outcome* is not in :data:`VALID_OUTCOMES`.
    """
    if outcome not in VALID_OUTCOMES:
        raise ValueError(
            f"Invalid outcome {outcome!r}. Must be one of: "
            + ", ".join(sorted(VALID_OUTCOMES))
        )

    init_outreach_log_schema(conn)

    row = conn.execute(
        """
        SELECT id FROM outreach_log
        WHERE lead_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (lead_id,),
    ).fetchone()

    if not row:
        logger.warning(
            "record_outcome: no outreach row found for lead_id=%s", lead_id
        )
        return False

    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        UPDATE outreach_log
        SET outcome = ?, outcome_at = ?,
            notes = CASE WHEN notes = '' THEN ? ELSE notes || ' | ' || ? END
        WHERE id = ?
        """,
        (outcome, now, notes, notes, row[0]),
    )
    conn.commit()
    logger.info(
        "Outcome recorded: lead_id=%s outcome=%s", lead_id, outcome
    )

    if outcome == "opted_out":
        _propagate_opt_out(conn, lead_id)

    return True


def _propagate_opt_out(conn: sqlite3.Connection, lead_id: int) -> None:
    """Add the opted-out lead to the suppression list.

    Looks up the ``leads`` row and passes causante/heir_name and
    contact_phone through to :func:`nadia_ai.suppression.add_suppression`.
    """
    # Import here to avoid a circular dependency at module load time.
    from nadia_ai.suppression import add_suppression  # noqa: PLC0415

    lead_row = conn.execute(
        "SELECT causante, heir_name, contact_phone, contact_email "
        "FROM leads WHERE id = ?",
        (lead_id,),
    ).fetchone()

    if lead_row is None:
        logger.warning(
            "_propagate_opt_out: lead_id=%s not found in leads table", lead_id
        )
        return

    # sqlite3.Row or tuple — normalise to indexed access.
    if hasattr(lead_row, "keys"):
        causante = lead_row["causante"]
        heir_name = lead_row["heir_name"]
        phone = lead_row["contact_phone"]
        email = lead_row["contact_email"]
    else:
        causante, heir_name, phone, email = lead_row

    name = causante or heir_name or None
    added = add_suppression(
        conn,
        name=name,
        phone=phone,
        email=email,
        reason="opted_out",
        source="outreach_log",
    )
    if added:
        logger.info(
            "opt-out propagated to suppression_list: lead_id=%s name=%r",
            lead_id,
            name,
        )


# ---------------------------------------------------------------------------
# Funnel analytics
# ---------------------------------------------------------------------------

def funnel(conn: sqlite3.Connection) -> dict:
    """Return conversion-funnel counts and rates.

    The funnel is::

        contacted → responded → interested → listed

    *contacted*  — distinct leads with at least one outreach row.
    *responded*  — contacted leads whose latest outcome is not 'pending'
                   and not 'no_answer'.
    *interested* — outcome in {'interested', 'listed'}.
    *listed*     — outcome == 'listed'.

    Returns a dict with keys: ``contacted``, ``responded``, ``interested``,
    ``listed``, ``response_rate``, ``interest_rate``, ``listing_rate``,
    ``opted_out``.  Rates are floats in [0, 1] (0.0 when the denominator is 0).
    """
    init_outreach_log_schema(conn)

    rows = conn.execute(
        """
        SELECT
            COUNT(DISTINCT lead_id) AS contacted,
            COUNT(DISTINCT CASE
                WHEN outcome NOT IN ('pending', 'no_answer')
                THEN lead_id END)  AS responded,
            COUNT(DISTINCT CASE
                WHEN outcome IN ('interested', 'listed')
                THEN lead_id END)  AS interested,
            COUNT(DISTINCT CASE
                WHEN outcome = 'listed'
                THEN lead_id END)  AS listed,
            COUNT(DISTINCT CASE
                WHEN outcome = 'opted_out'
                THEN lead_id END)  AS opted_out
        FROM (
            -- latest outcome row per lead
            SELECT lead_id, outcome
            FROM outreach_log
            WHERE id IN (
                SELECT MAX(id) FROM outreach_log GROUP BY lead_id
            )
        )
        """
    ).fetchone()

    if hasattr(rows, "keys"):
        contacted = rows["contacted"]
        responded = rows["responded"]
        interested = rows["interested"]
        listed_n = rows["listed"]
        opted_out = rows["opted_out"]
    else:
        contacted, responded, interested, listed_n, opted_out = rows

    def _rate(num: int, den: int) -> float:
        return round(num / den, 4) if den else 0.0

    return {
        "contacted": contacted,
        "responded": responded,
        "interested": interested,
        "listed": listed_n,
        "opted_out": opted_out,
        "response_rate": _rate(responded, contacted),
        "interest_rate": _rate(interested, responded),
        "listing_rate": _rate(listed_n, interested),
    }
