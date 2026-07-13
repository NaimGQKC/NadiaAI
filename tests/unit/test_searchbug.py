"""Searchbug heir-phone enrichment tests (parsing + opt-in gating)."""

import nadia_ai.enrich_searchbug as sb


def test_phones_from_handles_string_dict_and_rawtext():
    assert sb._phones_from({"phone": "+34 600 11 22 33"}) == ["+34 600 11 22 33"]
    assert sb._phones_from({"phones": [{"number": "600112233"}]}) == ["600112233"]
    # XML fallback: scan raw text for phone-shaped tokens
    assert sb._phones_from({"_raw_text": "<r><Phone>611 22 33 44</Phone></r>"}) == ["611 22 33 44"]


def test_phones_from_empty_or_garbage():
    assert sb._phones_from(None) == []
    assert sb._phones_from({"name": "Pedro", "emails": ["a@b.es"]}) == []


def test_split_name_spanish_two_surnames():
    assert sb._split_name("Pedro Ruiz Lopez") == ("Pedro", "Ruiz Lopez")
    assert sb._split_name("Pedro") == ("Pedro", "")
    assert sb._split_name("") == ("", "")


def test_clean_phone_prefers_es_then_falls_back():
    assert sb._clean_phone("600 11 22 33") == "600112233"   # 9-digit ES
    assert sb._clean_phone("+1 (415) 555-2671") == "14155552671"  # non-ES kept raw
    assert sb._clean_phone("nope") == ""


def test_resolve_disabled_without_key(monkeypatch, db_conn):
    monkeypatch.setattr(sb, "RESOLVE_HEIR_PHONE", False)
    monkeypatch.setattr(sb, "SEARCHBUG_API_KEY", "")
    assert sb.resolve_heir_phones(db_conn) == 0


def test_resolve_writes_phone_when_enabled(monkeypatch, db_conn):
    db_conn.execute(
        "INSERT INTO leads (id, tier, heir_name, localidad, contact_phone) "
        "VALUES (1, 'A', 'Pedro Ruiz Lopez', 'Zaragoza', '')"
    )
    db_conn.commit()
    monkeypatch.setattr(sb, "RESOLVE_HEIR_PHONE", True)
    monkeypatch.setattr(sb, "SEARCHBUG_API_KEY", "test-key")
    monkeypatch.setattr(sb, "_lookup", lambda name, loc: {"phone": "+34 600 11 22 33"})

    n = sb.resolve_heir_phones(db_conn)
    assert n == 1
    row = db_conn.execute("SELECT contact_phone, contact_source FROM leads WHERE id=1").fetchone()
    assert row["contact_phone"]
    assert row["contact_source"] == "searchbug"
