"""Tests for nadia_ai.tenancy — multi-tenant lead scoping.

Uses the shared ``db_conn`` in-memory fixture from tests/conftest.py which
already calls ``init_db`` + ``init_leads_schema``.  Tenancy tests layer
``init_tenancy_schema`` on top.
"""

from __future__ import annotations

import json

import pytest

from nadia_ai.tenancy import (
    _lead_in_territory,
    _norm,
    _territory_norms,
    add_tenant,
    get_tenant,
    init_tenancy_schema,
    leads_for_tenant,
    list_tenants,
    set_tenant_active,
    update_territory,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _schema(db_conn):
    """Ensure tenancy schema exists in every test (idempotent call)."""
    init_tenancy_schema(db_conn)


def _insert_lead(db_conn, *, localidad="Zaragoza", region="Zaragoza", tier="A",
                 causante="Test Persona", edict_window_days=None):
    """Insert a minimal lead row directly; returns the new row id."""
    cur = db_conn.execute(
        """INSERT INTO leads
           (causante, localidad, region, tier, outreach_allowed,
            sources, source_urls, heir_names_json, edict_window_days)
           VALUES (?, ?, ?, ?, 1, '[]', '[]', '[]', ?)""",
        (causante, localidad, region, tier, edict_window_days),
    )
    db_conn.commit()
    return cur.lastrowid


def _make_tenant(db_conn, territory=None, name="Agente Test"):
    """Add a tenant and return the full tenant dict."""
    tid = add_tenant(db_conn, name=name, territory=territory or [])
    return get_tenant(db_conn, tid)


# ── Schema / CRUD ─────────────────────────────────────────────────────────────


def test_init_tenancy_schema_is_idempotent(db_conn):
    init_tenancy_schema(db_conn)  # second call must not raise
    init_tenancy_schema(db_conn)
    count = db_conn.execute("SELECT COUNT(*) FROM tenants").fetchone()[0]
    assert count == 0


def test_add_tenant_basic(db_conn):
    tid = add_tenant(db_conn, name="María López", agency="RE/MAX", phone="600111222",
                     territory=["Zaragoza", "Huesca"])
    assert isinstance(tid, int) and tid > 0

    t = get_tenant(db_conn, tid)
    assert t["name"] == "María López"
    assert t["agency"] == "RE/MAX"
    assert t["phone"] == "600111222"
    assert t["territory_json"] == ["Zaragoza", "Huesca"]
    assert t["active"] == 1


def test_add_tenant_empty_territory(db_conn):
    tid = add_tenant(db_conn, name="Sin Territorio")
    t = get_tenant(db_conn, tid)
    assert t["territory_json"] == []


def test_add_tenant_rejects_blank_name(db_conn):
    with pytest.raises(ValueError, match="name"):
        add_tenant(db_conn, name="   ")


def test_get_tenant_missing_returns_none(db_conn):
    assert get_tenant(db_conn, 9999) is None


def test_list_tenants_order_newest_first(db_conn):
    add_tenant(db_conn, name="Primero")
    add_tenant(db_conn, name="Segundo")
    add_tenant(db_conn, name="Tercero")
    tenants = list_tenants(db_conn)
    assert [t["name"] for t in tenants] == ["Tercero", "Segundo", "Primero"]


def test_list_tenants_active_only_filter(db_conn):
    t1 = add_tenant(db_conn, name="Activo")
    t2 = add_tenant(db_conn, name="Inactivo")
    set_tenant_active(db_conn, t2, False)

    all_t = list_tenants(db_conn)
    active_t = list_tenants(db_conn, active_only=True)
    assert len(all_t) == 2
    assert len(active_t) == 1
    assert active_t[0]["name"] == "Activo"


def test_set_tenant_active_toggle(db_conn):
    tid = add_tenant(db_conn, name="Toggle Me", active=True)
    assert get_tenant(db_conn, tid)["active"] == 1

    set_tenant_active(db_conn, tid, False)
    assert get_tenant(db_conn, tid)["active"] == 0

    set_tenant_active(db_conn, tid, True)
    assert get_tenant(db_conn, tid)["active"] == 1


def test_set_tenant_active_missing_returns_false(db_conn):
    ok = set_tenant_active(db_conn, 9999, False)
    assert ok is False


def test_update_territory(db_conn):
    tid = add_tenant(db_conn, name="Agente A", territory=["Zaragoza"])
    ok = update_territory(db_conn, tid, ["Madrid", "Barcelona"])
    assert ok is True
    t = get_tenant(db_conn, tid)
    assert t["territory_json"] == ["Madrid", "Barcelona"]


def test_update_territory_missing_returns_false(db_conn):
    ok = update_territory(db_conn, 9999, ["Zaragoza"])
    assert ok is False


# ── Normalisation helpers ─────────────────────────────────────────────────────


def test_norm_strips_accents_and_lowercases():
    assert _norm("Zaragoza") == "zaragoza"
    assert _norm("Málag a") == "malag a"
    assert _norm(None) == ""
    assert _norm("") == ""


def test_territory_norms_deduplicates():
    norms = _territory_norms(["Zaragoza", "ZARAGOZA", "zaragoza", "Huesca"])
    assert "zaragoza" in norms
    assert "huesca" in norms
    assert len(norms) == 2  # 3 Zaragoza variants collapse to 1


# ── _lead_in_territory ────────────────────────────────────────────────────────


def test_lead_matched_by_region(db_conn):
    norms = _territory_norms(["Zaragoza"])
    lead = {"region": "Zaragoza", "localidad": "Calatayud"}
    assert _lead_in_territory(lead, norms) is True


def test_lead_matched_by_localidad_when_region_missing(db_conn):
    norms = _territory_norms(["Calatayud"])
    lead = {"region": "", "localidad": "Calatayud"}
    assert _lead_in_territory(lead, norms) is True


def test_lead_not_matched_outside_territory(db_conn):
    norms = _territory_norms(["Zaragoza"])
    lead = {"region": "Madrid", "localidad": "Madrid"}
    assert _lead_in_territory(lead, norms) is False


def test_lead_matched_accent_insensitive():
    """Territory token 'Malaga' matches lead.region 'Málaga'."""
    norms = _territory_norms(["Malaga"])
    lead = {"region": "Málaga", "localidad": "Málaga"}
    assert _lead_in_territory(lead, norms) is True


def test_lead_not_matched_empty_territory():
    norms = _territory_norms([])
    lead = {"region": "Zaragoza", "localidad": "Zaragoza"}
    assert _lead_in_territory(lead, norms) is False


# ── leads_for_tenant ─────────────────────────────────────────────────────────


def test_leads_for_tenant_returns_only_territory_leads(db_conn):
    _insert_lead(db_conn, localidad="Zaragoza", region="Zaragoza")
    _insert_lead(db_conn, localidad="Madrid", region="Madrid")
    _insert_lead(db_conn, localidad="Calatayud", region="Zaragoza")

    tenant = _make_tenant(db_conn, territory=["Zaragoza"])
    result = leads_for_tenant(db_conn, tenant)
    regions = {l["region"] for l in result}
    assert regions == {"Zaragoza"}
    assert len(result) == 2


def test_leads_for_tenant_empty_territory_returns_nothing(db_conn):
    _insert_lead(db_conn, localidad="Zaragoza", region="Zaragoza")
    tenant = _make_tenant(db_conn, territory=[])
    result = leads_for_tenant(db_conn, tenant)
    assert result == []


def test_leads_for_tenant_multiple_provinces(db_conn):
    _insert_lead(db_conn, localidad="Zaragoza", region="Zaragoza")
    _insert_lead(db_conn, localidad="Huesca", region="Huesca")
    _insert_lead(db_conn, localidad="Madrid", region="Madrid")

    tenant = _make_tenant(db_conn, territory=["Zaragoza", "Huesca"])
    result = leads_for_tenant(db_conn, tenant)
    assert len(result) == 2
    regions = {l["region"] for l in result}
    assert regions == {"Zaragoza", "Huesca"}


def test_leads_for_tenant_territory_json_as_string(db_conn):
    """tenant dict with territory_json as raw JSON string (as-stored in DB) still works."""
    _insert_lead(db_conn, localidad="Teruel", region="Teruel")
    tid = add_tenant(db_conn, name="Teruel Agent", territory=["Teruel"])
    # Simulate how it looks when fetched from DB without deserialisation
    tenant_raw = {
        "id": tid, "name": "Teruel Agent",
        "territory_json": json.dumps(["Teruel"]),  # raw string, not list
    }
    result = leads_for_tenant(db_conn, tenant_raw)
    assert len(result) == 1
    assert result[0]["region"] == "Teruel"


def test_leads_for_tenant_no_data_bleed_between_agents(db_conn):
    """Two agents in different territories must not see each other's leads."""
    _insert_lead(db_conn, localidad="Zaragoza", region="Zaragoza", causante="Lead Ara")
    _insert_lead(db_conn, localidad="Madrid", region="Madrid", causante="Lead Mad")

    aragon_agent = _make_tenant(db_conn, territory=["Zaragoza"], name="Agente Aragón")
    madrid_agent = _make_tenant(db_conn, territory=["Madrid"], name="Agente Madrid")

    ara_leads = leads_for_tenant(db_conn, aragon_agent)
    mad_leads = leads_for_tenant(db_conn, madrid_agent)

    assert all(l["region"] == "Zaragoza" for l in ara_leads)
    assert all(l["region"] == "Madrid" for l in mad_leads)
    # No lead appears in both lists
    ara_ids = {l["id"] for l in ara_leads}
    mad_ids = {l["id"] for l in mad_leads}
    assert ara_ids.isdisjoint(mad_ids)


def test_leads_for_tenant_overlapping_territories(db_conn):
    """Two agents in the same province both get those leads (overlap is intentional)."""
    _insert_lead(db_conn, localidad="Zaragoza", region="Zaragoza")

    agent_a = _make_tenant(db_conn, territory=["Zaragoza"], name="Agente A")
    agent_b = _make_tenant(db_conn, territory=["Zaragoza", "Huesca"], name="Agente B")

    leads_a = leads_for_tenant(db_conn, agent_a)
    leads_b = leads_for_tenant(db_conn, agent_b)

    # Both see the Zaragoza lead
    assert len(leads_a) == 1
    assert len(leads_b) == 1
    assert leads_a[0]["id"] == leads_b[0]["id"]


def test_leads_for_tenant_extra_where_filter(db_conn):
    """extra_where parameter correctly narrows the query."""
    _insert_lead(db_conn, localidad="Zaragoza", region="Zaragoza", tier="A")
    _insert_lead(db_conn, localidad="Zaragoza", region="Zaragoza", tier="C")

    tenant = _make_tenant(db_conn, territory=["Zaragoza"])
    tier_a = leads_for_tenant(db_conn, tenant, extra_where="AND tier = ?", params=("A",))
    assert all(l["tier"] == "A" for l in tier_a)
    assert len(tier_a) == 1


def test_leads_for_tenant_matched_by_localidad_fallback(db_conn):
    """Lead with blank region but matching localidad is included."""
    _insert_lead(db_conn, localidad="Ejea", region="")   # region blank

    tenant = _make_tenant(db_conn, territory=["Ejea"])
    result = leads_for_tenant(db_conn, tenant)
    assert len(result) == 1


def test_leads_for_tenant_returns_dicts(db_conn):
    _insert_lead(db_conn, localidad="Zaragoza", region="Zaragoza")
    tenant = _make_tenant(db_conn, territory=["Zaragoza"])
    result = leads_for_tenant(db_conn, tenant)
    assert all(isinstance(l, dict) for l in result)
    assert "causante" in result[0]
