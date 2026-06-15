"""Tests for outreach logging, outcome tracking, and funnel analytics.

Uses the shared ``db_conn`` in-memory fixture from ``tests/conftest.py``,
which initialises both ``nadia_ai.db.init_db`` (creates outreach_log base
schema) and ``nadia_ai.merge.init_leads_schema`` (creates the leads table).

Test coverage:
  * No double-contact within the cooldown window.
  * Contact IS allowed once the cooldown window has elapsed.
  * Different channels for the same lead are each logged independently.
  * record_outcome updates the most-recent row.
  * opted_out outcome propagates to suppression_list.
  * record_outcome with an invalid outcome raises ValueError.
  * funnel() returns correct counts and rates.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from nadia_ai.outreach_log import (
    VALID_OUTCOMES,
    funnel,
    init_outreach_log_schema,
    log_outreach,
    record_outcome,
)
from nadia_ai.suppression import init_suppression_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_lead(conn: sqlite3.Connection, lead_id: int, causante: str = "Test Causante",
                 phone: str | None = None, email: str | None = None) -> None:
    """Insert a minimal row into leads so foreign-key checks pass."""
    conn.execute(
        """
        INSERT OR IGNORE INTO leads (id, causante, contact_phone, contact_email,
                                     causante_norm, first_seen_at, last_updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (lead_id, causante, phone, email, causante.lower()),
    )
    conn.commit()


def _backdate_row(conn: sqlite3.Connection, lead_id: int, days: int) -> None:
    """Shift the created_at of outreach rows for a lead back by *days* days."""
    past = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    conn.execute(
        "UPDATE outreach_log SET created_at = ? WHERE lead_id = ?",
        (past, lead_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Schema / init
# ---------------------------------------------------------------------------

def test_init_is_idempotent(db_conn):
    """Calling init_outreach_log_schema multiple times must not raise."""
    init_outreach_log_schema(db_conn)
    init_outreach_log_schema(db_conn)
    cols = [r[1] for r in db_conn.execute("PRAGMA table_info(outreach_log)")]
    assert "outcome" in cols
    assert "tipo" in cols
    assert "outcome_at" in cols


# ---------------------------------------------------------------------------
# log_outreach — no double-contact within window
# ---------------------------------------------------------------------------

def test_first_contact_is_logged(db_conn):
    _insert_lead(db_conn, 1)
    result = log_outreach(db_conn, 1, "call", "FSBO")
    assert result is True
    n = db_conn.execute("SELECT COUNT(*) FROM outreach_log WHERE lead_id=1").fetchone()[0]
    assert n == 1


def test_second_contact_same_channel_within_window_is_skipped(db_conn):
    _insert_lead(db_conn, 2)
    log_outreach(db_conn, 2, "whatsapp", "BOE-N")
    result = log_outreach(db_conn, 2, "whatsapp", "BOE-N")
    assert result is False
    n = db_conn.execute("SELECT COUNT(*) FROM outreach_log WHERE lead_id=2").fetchone()[0]
    assert n == 1  # still only one row


def test_different_channels_are_logged_independently(db_conn):
    """call and whatsapp are distinct channels — both should be logged."""
    _insert_lead(db_conn, 3)
    r1 = log_outreach(db_conn, 3, "call", "FSBO")
    r2 = log_outreach(db_conn, 3, "whatsapp", "FSBO")
    assert r1 is True
    assert r2 is True
    n = db_conn.execute("SELECT COUNT(*) FROM outreach_log WHERE lead_id=3").fetchone()[0]
    assert n == 2


def test_contact_allowed_after_cooldown_expires(db_conn):
    """After the cooldown window passes, re-contact IS allowed."""
    _insert_lead(db_conn, 4)
    log_outreach(db_conn, 4, "letter", "Heraldo", cooldown_days=30)
    # Simulate the row being written 31 days ago.
    _backdate_row(db_conn, 4, days=31)
    result = log_outreach(db_conn, 4, "letter", "Heraldo", cooldown_days=30)
    assert result is True
    n = db_conn.execute("SELECT COUNT(*) FROM outreach_log WHERE lead_id=4").fetchone()[0]
    assert n == 2


def test_default_outcome_is_pending(db_conn):
    _insert_lead(db_conn, 5)
    log_outreach(db_conn, 5, "call", "FSBO")
    row = db_conn.execute(
        "SELECT outcome FROM outreach_log WHERE lead_id=5"
    ).fetchone()
    assert row[0] == "pending"


# ---------------------------------------------------------------------------
# record_outcome
# ---------------------------------------------------------------------------

def test_record_outcome_updates_latest_row(db_conn):
    _insert_lead(db_conn, 6)
    log_outreach(db_conn, 6, "call", "FSBO")
    updated = record_outcome(db_conn, 6, "interested")
    assert updated is True
    row = db_conn.execute(
        "SELECT outcome, outcome_at FROM outreach_log WHERE lead_id=6"
    ).fetchone()
    assert row[0] == "interested"
    assert row[1] is not None  # outcome_at was stamped


def test_record_outcome_with_no_log_row_returns_false(db_conn):
    """No outreach row exists yet — should return False, not raise."""
    updated = record_outcome(db_conn, 999, "no_answer")
    assert updated is False


def test_record_outcome_rejects_invalid_outcome(db_conn):
    _insert_lead(db_conn, 7)
    log_outreach(db_conn, 7, "call", "FSBO")
    with pytest.raises(ValueError, match="Invalid outcome"):
        record_outcome(db_conn, 7, "unknown_state")


def test_all_valid_outcomes_are_accepted(db_conn):
    for idx, outcome in enumerate(sorted(VALID_OUTCOMES), start=100):
        _insert_lead(db_conn, idx)
        log_outreach(db_conn, idx, "call", "test")
        assert record_outcome(db_conn, idx, outcome) is True


# ---------------------------------------------------------------------------
# opted_out → suppression propagation
# ---------------------------------------------------------------------------

def test_opted_out_propagates_to_suppression_list(db_conn):
    """When outcome == opted_out, the lead must appear in suppression_list."""
    init_suppression_schema(db_conn)
    _insert_lead(db_conn, 10, causante="María García López", phone="976123456")

    log_outreach(db_conn, 10, "call", "FSBO")
    record_outcome(db_conn, 10, "opted_out", notes="No quiere ser contactada")

    rows = db_conn.execute("SELECT * FROM suppression_list WHERE phone='976123456'").fetchall()
    assert len(rows) == 1
    row = rows[0]
    # reason should be 'opted_out', source should be 'outreach_log'
    assert row[4] == "opted_out"   # reason column (index 4)
    assert row[5] == "outreach_log"  # source column (index 5)


def test_opted_out_is_idempotent_in_suppression(db_conn):
    """Recording opted_out twice must not create duplicate suppression rows."""
    init_suppression_schema(db_conn)
    _insert_lead(db_conn, 11, causante="Pedro Martínez Ruiz", phone="612000111")

    log_outreach(db_conn, 11, "whatsapp", "BOE-N")
    record_outcome(db_conn, 11, "opted_out")

    # Second outreach + opted_out on the same lead.
    log_outreach(db_conn, 11, "letter", "BOE-N")
    record_outcome(db_conn, 11, "opted_out")

    n = db_conn.execute(
        "SELECT COUNT(*) FROM suppression_list WHERE phone='612000111'"
    ).fetchone()[0]
    assert n == 1  # idempotent — suppression.add_suppression deduplicates


def test_opted_out_without_lead_row_does_not_raise(db_conn):
    """If the lead row disappears after logging, opted_out must not crash."""
    init_suppression_schema(db_conn)
    # Insert the lead, log outreach, then remove the lead row to simulate the
    # edge case where the leads row was deleted between logging and outcome.
    _insert_lead(db_conn, 888, causante="Fantasma Lead")
    log_outreach(db_conn, 888, "call", "FSBO")
    # Remove the lead row — FK check must be off for this, so turn it off momentarily.
    db_conn.execute("PRAGMA foreign_keys=OFF")
    db_conn.execute("DELETE FROM leads WHERE id=888")
    db_conn.commit()
    db_conn.execute("PRAGMA foreign_keys=ON")
    # record_outcome should warn but NOT raise.
    result = record_outcome(db_conn, 888, "opted_out")
    assert result is True


# ---------------------------------------------------------------------------
# funnel()
# ---------------------------------------------------------------------------

def _setup_funnel_data(conn: sqlite3.Connection) -> None:
    """Insert leads 20–25 and a variety of outreach outcomes."""
    init_outreach_log_schema(conn)
    for i in range(20, 26):
        _insert_lead(conn, i, causante=f"Persona {i}")

    scenarios = [
        (20, "call", "FSBO", "interested"),
        (21, "call", "FSBO", "listed"),
        (22, "whatsapp", "BOE-N", "not_interested"),
        (23, "letter", "Heraldo", "no_answer"),
        (24, "call", "FSBO", "opted_out"),
        (25, "call", "FSBO", "pending"),
    ]
    for lead_id, channel, tipo, outcome in scenarios:
        log_outreach(conn, lead_id, channel, tipo)
        if outcome != "pending":
            record_outcome(conn, lead_id, outcome)


def test_funnel_contacted_count(db_conn):
    _setup_funnel_data(db_conn)
    f = funnel(db_conn)
    assert f["contacted"] == 6


def test_funnel_responded_excludes_pending_and_no_answer(db_conn):
    _setup_funnel_data(db_conn)
    f = funnel(db_conn)
    # responded = not_interested(22) + interested(20) + listed(21) + opted_out(24) = 4
    assert f["responded"] == 4


def test_funnel_interested_includes_listed(db_conn):
    _setup_funnel_data(db_conn)
    f = funnel(db_conn)
    # interested = interested(20) + listed(21) = 2
    assert f["interested"] == 2


def test_funnel_listed_count(db_conn):
    _setup_funnel_data(db_conn)
    f = funnel(db_conn)
    assert f["listed"] == 1


def test_funnel_opted_out_count(db_conn):
    _setup_funnel_data(db_conn)
    f = funnel(db_conn)
    assert f["opted_out"] == 1


def test_funnel_response_rate(db_conn):
    _setup_funnel_data(db_conn)
    f = funnel(db_conn)
    # 4 responded / 6 contacted
    assert abs(f["response_rate"] - round(4 / 6, 4)) < 1e-6


def test_funnel_interest_rate(db_conn):
    _setup_funnel_data(db_conn)
    f = funnel(db_conn)
    # 2 interested / 4 responded
    assert abs(f["interest_rate"] - round(2 / 4, 4)) < 1e-6


def test_funnel_listing_rate(db_conn):
    _setup_funnel_data(db_conn)
    f = funnel(db_conn)
    # 1 listed / 2 interested
    assert abs(f["listing_rate"] - round(1 / 2, 4)) < 1e-6


def test_funnel_empty_db_returns_zeros(db_conn):
    """An empty log should return all zeros without division errors."""
    f = funnel(db_conn)
    assert f["contacted"] == 0
    assert f["response_rate"] == 0.0
    assert f["interest_rate"] == 0.0
    assert f["listing_rate"] == 0.0
