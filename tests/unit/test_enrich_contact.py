"""Tests for enrich_contact.py (search-native contact discovery).

Covers:
- Tolerant JSON parsing of search-model output (fences / surrounding prose)
- discover_contact: identity gating (match vs no-match), invalid-name short-circuit
- run_contact_enrichment: tier filter, DB write, enriched-at marking, count
- No-provider safety (returns 0, writes nothing)
All network calls are mocked — no real API traffic.
"""

import sqlite3

import nadia_ai.enrich_contact as ec
from nadia_ai.enrich_contact import (
    ContactResult,
    _parse_json_blob,
    discover_contact,
    enrich_lead,
    get_leads_for_contact,
    run_contact_enrichment,
)
from nadia_ai.merge import init_leads_schema


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_leads_schema(conn)
    return conn


def _insert_lead(conn: sqlite3.Connection, **kwargs) -> int:
    defaults = {
        "causante": "JUAN PEREZ GARCIA",
        "heir_name": "MARIA PEREZ LOPEZ",
        "heir_names_json": "[]",
        "localidad": "Zaragoza",
        "tier": "A",
        "outreach_allowed": 1,
        "days_since_death": 10,
        "sources": "[]",
        "source_urls": "[]",
    }
    defaults.update(kwargs)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join("?" for _ in defaults)
    cur = conn.execute(
        f"INSERT INTO leads ({cols}) VALUES ({placeholders})", tuple(defaults.values())
    )
    conn.commit()
    return cur.lastrowid


# ── JSON parsing ───────────────────────────────────────────────────────────

def test_parse_plain_json():
    assert _parse_json_blob('{"a": 1}') == {"a": 1}


def test_parse_fenced_json():
    text = '```json\n{"phone": "976123456"}\n```'
    assert _parse_json_blob(text) == {"phone": "976123456"}


def test_parse_json_with_surrounding_prose():
    text = 'Claro, aquí tienes:\n{"identity_match": true}\nEspero que ayude.'
    assert _parse_json_blob(text) == {"identity_match": True}


def test_parse_garbage_returns_none():
    assert _parse_json_blob("no json here") is None
    assert _parse_json_blob("") is None


# ── discover_contact ───────────────────────────────────────────────────────

def test_invalid_name_short_circuits(monkeypatch):
    """A boilerplate name must never trigger a paid search."""
    called = {"n": 0}

    def _spy(_prompt):
        called["n"] += 1
        return None

    monkeypatch.setattr(ec, "_search_via_perplexity", _spy)
    out = discover_contact("En Cualquier Caso", "Zaragoza", "herencia")
    assert out["identity_match"] is False
    assert called["n"] == 0


def test_identity_match_returns_contact(monkeypatch):
    payload = {
        "identity_match": True,
        "confidence": "high",
        "phone": "976123456",
        "email": None,
        "profile_url": "https://linkedin.com/in/maria",
    }
    monkeypatch.setattr(
        ec, "_search_via_perplexity", lambda p: (payload, ["https://paginasblancas.es/x"])
    )
    out = discover_contact("Maria Perez Lopez", "Zaragoza", "herencia")
    assert out["identity_match"] is True
    assert out["phone"] == "976123456"
    assert out["source_url"] == "https://paginasblancas.es/x"


def test_no_identity_match_nulls_contact(monkeypatch):
    payload = {"identity_match": False, "confidence": "low", "phone": "976000000"}
    monkeypatch.setattr(ec, "_search_via_perplexity", lambda p: (payload, []))
    out = discover_contact("Maria Perez Lopez", "Zaragoza", "herencia")
    assert out["identity_match"] is False
    assert out["phone"] is None  # untrusted phone is dropped


def test_low_confidence_is_dropped(monkeypatch):
    payload = {"identity_match": True, "confidence": "low", "phone": "976000000"}
    monkeypatch.setattr(ec, "_search_via_perplexity", lambda p: (payload, []))
    out = discover_contact("Maria Perez Lopez", "Zaragoza", "herencia")
    assert out["phone"] is None


def test_provider_unavailable_returns_none(monkeypatch):
    monkeypatch.setattr(ec, "_search_via_perplexity", lambda p: None)
    assert discover_contact("Maria Perez Lopez", "Zaragoza", "herencia") is None


# ── run_contact_enrichment ─────────────────────────────────────────────────

def test_no_provider_key_skips(monkeypatch):
    monkeypatch.setattr(ec, "PERPLEXITY_API_KEY", "")
    monkeypatch.setattr(ec, "GEMINI_API_KEY", "")
    conn = _make_conn()
    _insert_lead(conn)
    assert run_contact_enrichment(conn) == 0


def test_tier_filter_excludes_c_and_x(monkeypatch):
    monkeypatch.setattr(ec, "PERPLEXITY_API_KEY", "test")
    conn = _make_conn()
    _insert_lead(conn, tier="A")
    _insert_lead(conn, tier="B")
    _insert_lead(conn, tier="C")
    _insert_lead(conn, tier="X", outreach_allowed=0)
    leads = get_leads_for_contact(conn, limit=50)
    assert {l["tier"] for l in leads} == {"A", "B"}


def test_enrichment_writes_and_marks(monkeypatch):
    monkeypatch.setattr(ec, "PERPLEXITY_API_KEY", "test")
    monkeypatch.setattr(
        ec,
        "discover_contact",
        lambda name, city, ctx: {
            "identity_match": True,
            "confidence": "high",
            "phone": "976123456",
            "email": None,
            "profile_url": None,
            "source_url": "https://paginasblancas.es/x",
        },
    )
    conn = _make_conn()
    lead_id = _insert_lead(conn)
    found = run_contact_enrichment(conn)
    assert found == 1
    row = conn.execute(
        "SELECT contact_phone, contact_confidence, contact_enriched_at FROM leads WHERE id = ?",
        (lead_id,),
    ).fetchone()
    assert row["contact_phone"] == "976123456"
    assert row["contact_confidence"] == "high"
    assert row["contact_enriched_at"]  # marked, won't be re-queried


def test_transient_failure_leaves_unmarked(monkeypatch):
    """If the provider is unavailable mid-run, the lead stays unmarked for retry."""
    monkeypatch.setattr(ec, "PERPLEXITY_API_KEY", "test")
    monkeypatch.setattr(ec, "discover_contact", lambda name, city, ctx: None)
    conn = _make_conn()
    lead_id = _insert_lead(conn)
    assert run_contact_enrichment(conn) == 0
    row = conn.execute(
        "SELECT contact_enriched_at FROM leads WHERE id = ?", (lead_id,)
    ).fetchone()
    assert not row["contact_enriched_at"]


# ── waterfall orchestrator ──────────────────────────────────────────────────

def test_waterfall_stops_on_first_hit(monkeypatch):
    """A higher-priority source that hits short-circuits the lower ones."""
    calls = []

    def _hit(lead, name, city, ctx):
        calls.append("einforma")
        return ContactResult(source="einforma", has_contact=True, phone="976111111")

    def _should_not_run(lead, name, city, ctx):
        calls.append("search_llm")
        return ContactResult(source="search_llm", has_contact=True, phone="999")

    monkeypatch.setattr(
        ec, "CONTACT_SOURCES", [("einforma", _hit), ("search_llm", _should_not_run)]
    )
    res = enrich_lead({"heir_name": "Maria Perez Lopez", "localidad": "Zaragoza", "sources": "[]"})
    assert res.source == "einforma"
    assert res.phone == "976111111"
    assert calls == ["einforma"]  # second tier never ran


def test_waterfall_cascades_past_negative(monkeypatch):
    """A source that ran-but-found-nothing falls through to the next."""
    def _miss(lead, name, city, ctx):
        return None  # defers

    def _hit(lead, name, city, ctx):
        return ContactResult(source="search_llm", has_contact=True, email="a@b.es")

    monkeypatch.setattr(
        ec, "CONTACT_SOURCES", [("einforma", _miss), ("search_llm", _hit)]
    )
    res = enrich_lead({"heir_name": "Maria Perez Lopez", "localidad": "Zaragoza", "sources": "[]"})
    assert res.source == "search_llm"
    assert res.email == "a@b.es"


def test_waterfall_all_defer_returns_none(monkeypatch):
    monkeypatch.setattr(ec, "CONTACT_SOURCES", [("x", lambda *a: None)])
    assert enrich_lead({"heir_name": "Maria Perez Lopez", "sources": "[]"}) is None


def test_b2b_lead_skips_person_sources():
    """A company lead (no heir, B2B source) is not web-searched as a person."""
    lead = {"heir_name": "", "causante": "BAR PEPE SL", "sources": "Traspasos"}
    # einforma stub returns None (no key), paginas/search_llm self-skip B2B → None
    assert enrich_lead(lead) is None


def test_contact_source_tag_persisted(monkeypatch):
    monkeypatch.setattr(ec, "PERPLEXITY_API_KEY", "test")
    monkeypatch.setattr(
        ec,
        "discover_contact",
        lambda name, city, ctx: {
            "identity_match": True, "confidence": "medium",
            "phone": "976123456", "email": None, "profile_url": None,
            "source_url": "https://x.es",
        },
    )
    conn = _make_conn()
    lead_id = _insert_lead(conn)
    run_contact_enrichment(conn)
    row = conn.execute(
        "SELECT contact_source FROM leads WHERE id = ?", (lead_id,)
    ).fetchone()
    assert row["contact_source"] == "search_llm"
