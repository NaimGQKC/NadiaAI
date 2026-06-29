"""RocketReach heir-phone enrichment tests (parsing + opt-in gating)."""

from unittest import mock

import nadia_ai.enrich_rocketreach as rr


def test_phones_from_handles_string_and_dict_lists():
    assert rr._phones_from({"phones": ["+34 600 11 22 33"]}) == ["+34 600 11 22 33"]
    assert rr._phones_from({"phone_numbers": [{"number": "600112233"}]}) == ["600112233"]
    # phone-ish key fallback + de-dup
    out = rr._phones_from({"mobile_phone": "611 22 33 44", "phones": ["611 22 33 44"]})
    assert out == ["611 22 33 44"]


def test_phones_from_empty_or_garbage():
    assert rr._phones_from(None) == []
    assert rr._phones_from({"name": "Pedro", "emails": ["a@b.es"]}) == []


def test_resolve_disabled_without_key(monkeypatch, db_conn):
    monkeypatch.setattr(rr, "RESOLVE_HEIR_PHONE", False)
    monkeypatch.setattr(rr, "ROCKETREACH_API_KEY", "")
    assert rr.resolve_heir_phones(db_conn) == 0


def test_resolve_writes_phone_when_enabled(monkeypatch, db_conn):
    db_conn.execute(
        "INSERT INTO leads (id, tier, heir_name, localidad, contact_phone) "
        "VALUES (1, 'A', 'Pedro Ruiz Lopez', 'Zaragoza', '')"
    )
    db_conn.commit()
    monkeypatch.setattr(rr, "RESOLVE_HEIR_PHONE", True)
    monkeypatch.setattr(rr, "ROCKETREACH_API_KEY", "test-key")
    monkeypatch.setattr(rr, "_lookup", lambda name, loc: {"phones": ["+34 600 11 22 33"]})

    n = rr.resolve_heir_phones(db_conn)
    assert n == 1
    row = db_conn.execute("SELECT contact_phone, contact_source FROM leads WHERE id=1").fetchone()
    assert row["contact_phone"]  # normalized phone stored
    assert row["contact_source"] == "rocketreach"
