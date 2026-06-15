"""Tests for the opt-out / suppression gate and the Art. 14 notice in outreach."""

import sqlite3

import pytest

from nadia_ai.outreach import RGPD_NOTICE_FULL, render_outreach
from nadia_ai.suppression import (
    add_suppression,
    filter_suppressed,
    init_suppression_schema,
    is_suppressed,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_suppression_schema(c)
    yield c
    c.close()


def test_add_requires_an_identifier(conn):
    with pytest.raises(ValueError):
        add_suppression(conn, reason="empty")


def test_add_is_idempotent(conn):
    assert add_suppression(conn, name="Juan Pérez García", reason="opt-out") is True
    assert add_suppression(conn, name="Juan Pérez García", reason="opt-out") is False
    n = conn.execute("SELECT COUNT(*) FROM suppression_list").fetchone()[0]
    assert n == 1


def test_suppress_by_name_matches_causante_and_any_heir(conn):
    # Honorifics/accents are normalised, so these all collide with the stored entry.
    add_suppression(conn, name="don Juan Perez Garcia")
    names, phones, emails = _sets(conn)
    assert is_suppressed({"causante": "Juan Pérez García"}, names, phones, emails)
    # An objector who is one of several heirs suppresses the whole (family) lead.
    assert is_suppressed(
        {"causante": "Otra Persona", "heir_names_json": '["Ana López", "Juan Perez Garcia"]'},
        names, phones, emails,
    )
    assert not is_suppressed({"causante": "Persona No Listada"}, names, phones, emails)


def test_suppress_by_phone_ignores_formatting(conn):
    add_suppression(conn, phone="+34 976 12 34 56", reason="queja")
    names, phones, emails = _sets(conn)
    assert is_suppressed({"contact_phone": "976123456"}, names, phones, emails)
    assert is_suppressed({"phone": "0034976123456"}, names, phones, emails)
    assert not is_suppressed({"contact_phone": "999000111"}, names, phones, emails)


def test_filter_suppressed_splits_and_counts(conn):
    add_suppression(conn, name="Bloqueado Uno")
    leads = [
        {"id": 1, "causante": "Bloqueado Uno"},
        {"id": 2, "causante": "Permitido Dos"},
        {"id": 3, "heir_name": "Bloqueado Uno"},
    ]
    allowed, removed = filter_suppressed(conn, leads)
    assert removed == 2
    assert [l["id"] for l in allowed] == [2]


def test_filter_is_noop_when_list_empty(conn):
    leads = [{"id": 1, "causante": "Quien Sea"}]
    allowed, removed = filter_suppressed(conn, leads)
    assert removed == 0 and allowed is leads


def test_art14_notice_attached_to_every_channel():
    # Inheritance / obituary letters carry the FULL Art. 14 notice...
    for lead in (
        {"subsource": "BOE-N", "causante": "Test Uno", "localidad": "Zaragoza", "id": 1},
        {"sources": '["Heraldo"]', "causante": "Test Dos", "localidad": "Zaragoza", "id": 2},
        {"subsource": "BOE-TEJU", "causante": "Test Tres", "localidad": "Zaragoza", "id": 3},
    ):
        msg = render_outreach(lead)["mensaje"]
        assert "RGPD" in msg
        assert "art. 6.1.f" in msg               # legal basis stated
        assert "aepd" in msg.lower()             # supervisory authority
        assert "oposición" in msg.lower()        # right to object
    # ...FSBO gets the lighter, source-appropriate disclosure (still names RGPD + opt-out).
    fsbo = render_outreach(
        {"source": "fsbo_pisos", "address": "Centro", "price_eur": 150000, "phone": "600", "id": 9}
    )["mensaje"]
    assert "RGPD" in fsbo and "pisos.com" in fsbo


def test_notice_has_no_unfilled_placeholders():
    import re

    msg = render_outreach({"subsource": "BOE-N", "causante": "X Y", "localidad": "Zaragoza"})["mensaje"]
    assert not re.search(r"\{[a-z_]+\}", msg)


def _sets(conn):
    rows = conn.execute("SELECT name_norm, phone, email FROM suppression_list").fetchall()
    return ({r[0] for r in rows if r[0]}, {r[1] for r in rows if r[1]}, {r[2] for r in rows if r[2]})
