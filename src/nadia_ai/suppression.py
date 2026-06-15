"""Opt-out / suppression list — the GDPR & LSSI guardrail for outreach.

Every outreach template tells the recipient they can ask to be removed ("puede
solicitar su supresión"). That promise is only real if we *honour* it. This module
is the do-not-contact ledger and the gate that enforces it:

  * ``add_suppression``  — record a person/contact who asked not to be contacted
    (or anyone we must never approach: a complaint, a lawyer's letter, an AEPD
    request). Also the home for a future Lista Robinson import.
  * ``is_suppressed`` / ``filter_suppressed`` — the gate. The outreach pack runs
    every candidate through this BEFORE rendering, so a suppressed person can
    never reappear in a worklist, no matter which source re-surfaces them.

Matching is by normalised name (causante OR *any* named heir — a family letter
reaches the whole family, so one objector suppresses the lead), or by exact phone
/ email. Name normalisation reuses ``merge.normalize_name`` so it lines up with the
rest of the pipeline (accents/honorifics stripped).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from nadia_ai.merge import normalize_name

logger = logging.getLogger("nadia_ai.suppression")


def init_suppression_schema(conn: sqlite3.Connection) -> None:
    """Create the suppression table + indexes (idempotent)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS suppression_list (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name_norm   TEXT,
            phone       TEXT,
            email       TEXT,
            reason      TEXT NOT NULL DEFAULT '',
            source      TEXT NOT NULL DEFAULT 'manual',
            added_at    TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_suppr_name ON suppression_list(name_norm)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_suppr_phone ON suppression_list(phone)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_suppr_email ON suppression_list(email)")
    conn.commit()


def _norm_phone(phone: str | None) -> str | None:
    """Digits only, so '+34 976 12 34 56' == '976123456' (last 9 kept)."""
    if not phone:
        return None
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    return digits[-9:] if len(digits) >= 9 else (digits or None)


def _norm_email(email: str | None) -> str | None:
    return email.strip().lower() or None if email else None


def add_suppression(
    conn: sqlite3.Connection,
    *,
    name: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    reason: str = "",
    source: str = "manual",
) -> bool:
    """Add a do-not-contact entry. Needs at least one of name/phone/email.

    Returns True if a row was written. Idempotent on the (name_norm, phone, email)
    triple so re-recording the same objection is a no-op."""
    init_suppression_schema(conn)
    name_norm = normalize_name(name)
    phone_n = _norm_phone(phone)
    email_n = _norm_email(email)
    if not any((name_norm, phone_n, email_n)):
        raise ValueError("add_suppression needs at least one of name / phone / email")

    dup = conn.execute(
        """SELECT 1 FROM suppression_list
           WHERE IFNULL(name_norm,'')=IFNULL(?,'')
             AND IFNULL(phone,'')=IFNULL(?,'')
             AND IFNULL(email,'')=IFNULL(?,'') LIMIT 1""",
        (name_norm, phone_n, email_n),
    ).fetchone()
    if dup:
        return False

    conn.execute(
        """INSERT INTO suppression_list (name_norm, phone, email, reason, source, added_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (name_norm, phone_n, email_n, reason, source, datetime.now(UTC).isoformat()),
    )
    conn.commit()
    logger.info("Suppression added (source=%s reason=%s)", source, reason or "—")
    return True


def _load_sets(conn: sqlite3.Connection) -> tuple[set, set, set]:
    init_suppression_schema(conn)
    rows = conn.execute("SELECT name_norm, phone, email FROM suppression_list").fetchall()
    names = {r[0] for r in rows if r[0]}
    phones = {r[1] for r in rows if r[1]}
    emails = {r[2] for r in rows if r[2]}
    return names, phones, emails


def _lead_names(lead: dict) -> set[str]:
    """All normalised names a lead would put in front of a recipient."""
    import json

    out: set[str] = set()
    for key in ("causante", "heir_name"):
        n = normalize_name(lead.get(key))
        if n:
            out.add(n)
    raw = lead.get("heir_names_json")
    if raw:
        try:
            for h in json.loads(raw):
                n = normalize_name(h)
                if n:
                    out.add(n)
        except (json.JSONDecodeError, TypeError):
            pass
    return out


def is_suppressed(lead: dict, names: set, phones: set, emails: set) -> bool:
    """True if this lead matches any suppression entry (name / phone / email)."""
    if _lead_names(lead) & names:
        return True
    for pkey in ("contact_phone", "phone"):
        if _norm_phone(lead.get(pkey)) in phones and _norm_phone(lead.get(pkey)):
            return True
    for ekey in ("contact_email", "email"):
        if _norm_email(lead.get(ekey)) in emails and _norm_email(lead.get(ekey)):
            return True
    return False


def filter_suppressed(conn: sqlite3.Connection, leads: list[dict]) -> tuple[list[dict], int]:
    """Split leads into (allowed, n_removed). The outreach gate.

    A loud, central choke point: nothing reaches a worklist without passing here."""
    names, phones, emails = _load_sets(conn)
    if not (names or phones or emails):
        return leads, 0
    allowed = [l for l in leads if not is_suppressed(l, names, phones, emails)]
    removed = len(leads) - len(allowed)
    if removed:
        logger.info("Suppression gate removed %d/%d leads from outreach", removed, len(leads))
    return allowed, removed
