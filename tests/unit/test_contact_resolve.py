"""Tests for contact_resolve.py (name+locality → reachable contact channel).

Covers:
- postal_resolver: always serves a valid name; confidence tracks address precision;
  declines only on an unusable name (the one case the waterfall may end empty).
- notary_resolver: self-selects to leads with a handling office; declines otherwise.
- registry_resolver / skiptrace_resolver: env-gated stubs — None until keyed.
- resolve_contact: waterfall order, first-hit short-circuit, guaranteed postal
  fallback, resolver-exception isolation.
- resolve_for_lead: lead-dict field mapping (heir > causante, office, address).
- ContactResolver Protocol: bare functions satisfy it (runtime_checkable).
No network, no DB — every resolver is a pure function of its inputs.
"""

import nadia_ai.contact_resolve as cr
from nadia_ai.contact_resolve import (
    CHANNEL_NOTARY,
    CHANNEL_POSTAL,
    CONF_HIGH,
    CONF_LOW,
    CONF_MEDIUM,
    ContactResolver,
    ResolvedContact,
    notary_resolver,
    postal_resolver,
    registry_resolver,
    resolve_contact,
    resolve_for_lead,
    skiptrace_resolver,
)


# ── postal_resolver (the always-available floor) ────────────────────────────

def test_postal_locality_only_is_medium():
    r = postal_resolver("María Pérez López", "Zaragoza")
    assert r is not None
    assert r.channel == CHANNEL_POSTAL
    assert r.confidence == CONF_MEDIUM
    assert "María Pérez López" in r.target
    assert "Zaragoza" in r.target
    assert "España" in r.target


def test_postal_with_street_is_high():
    r = postal_resolver("María Pérez López", "Zaragoza", address="Calle Mayor 3")
    assert r.confidence == CONF_HIGH
    assert "Calle Mayor 3" in r.target
    assert r.meta["has_street"] is True


def test_postal_name_only_is_low_but_still_serves():
    r = postal_resolver("María Pérez López", "")
    assert r is not None
    assert r.confidence == CONF_LOW
    assert "María Pérez López" in r.target


def test_postal_declines_on_unusable_name():
    """A blank/boilerplate name can't address an envelope — the one None case."""
    assert postal_resolver("", "Zaragoza") is None
    assert postal_resolver("   ", "Zaragoza") is None


def test_postal_preserves_display_name_casing():
    """normalize_name lowercases for matching; the printed label keeps the original."""
    r = postal_resolver("MARÍA PÉREZ", "Zaragoza")
    assert "MARÍA PÉREZ" in r.target


def test_postal_is_not_direct():
    r = postal_resolver("María Pérez", "Zaragoza")
    assert r.is_direct is False


# ── notary_resolver (public intermediary) ───────────────────────────────────

def test_notary_resolves_when_office_present():
    r = notary_resolver("María Pérez", "Zaragoza", office="Notaría de Juan Gómez de Zaragoza")
    assert r is not None
    assert r.channel == CHANNEL_NOTARY
    assert r.confidence == CONF_HIGH
    assert r.target == "Notaría de Juan Gómez de Zaragoza"


def test_notary_declines_without_office():
    assert notary_resolver("María Pérez", "Zaragoza") is None
    assert notary_resolver("María Pérez", "Zaragoza", office="  ") is None


def test_notary_is_indirect_channel():
    r = notary_resolver("María Pérez", "Zaragoza", office="Notaría X")
    assert r.is_direct is False  # reaches the heir only via the office


# ── env-gated paid stubs (off by default) ───────────────────────────────────

def test_registry_stub_defers_without_key(monkeypatch):
    monkeypatch.setattr(cr, "REGISTRO_INDICES_API_KEY", "")
    assert registry_resolver("María Pérez", "Zaragoza", ref_catastral="1234") is None


def test_registry_stub_still_stub_with_key(monkeypatch):
    """Keyed but unimplemented → still None (no real call shipped), never crashes."""
    monkeypatch.setattr(cr, "REGISTRO_INDICES_API_KEY", "test-key")
    assert registry_resolver("María Pérez", "Zaragoza") is None


def test_skiptrace_stub_defers_without_key(monkeypatch):
    monkeypatch.setattr(cr, "CONTACT_SKIPTRACE_API_KEY", "")
    assert skiptrace_resolver("María Pérez", "Zaragoza") is None


def test_skiptrace_stub_still_stub_with_key(monkeypatch):
    monkeypatch.setattr(cr, "CONTACT_SKIPTRACE_API_KEY", "test-key")
    assert skiptrace_resolver("María Pérez", "Zaragoza") is None


# ── resolve_contact (the waterfall) ─────────────────────────────────────────

def test_waterfall_default_falls_back_to_postal():
    """With both paid stubs off and no office, the heir still gets a postal target."""
    r = resolve_contact("María Pérez López", "Zaragoza")
    assert r is not None
    assert r.channel == CHANNEL_POSTAL


def test_waterfall_prefers_office_over_postal():
    """A notarial lead resolves to the notaría, not the weaker postal floor."""
    r = resolve_contact("María Pérez", "Zaragoza", office="Notaría de X de Zaragoza")
    assert r.channel == CHANNEL_NOTARY


def test_waterfall_stops_on_first_hit():
    calls = []

    def _hit(name, locality, **kw):
        calls.append("hit")
        return ResolvedContact(channel="phone", target="600111222", source="hit")

    def _never(name, locality, **kw):
        calls.append("never")
        return ResolvedContact(channel="phone", target="999", source="never")

    r = resolve_contact("María Pérez", "Zaragoza", resolvers=[("hit", _hit), ("never", _never)])
    assert r.source == "hit"
    assert calls == ["hit"]  # second resolver never ran


def test_waterfall_cascades_past_decliners():
    def _defer(name, locality, **kw):
        return None

    r = resolve_contact(
        "María Pérez", "Zaragoza",
        resolvers=[("a", _defer), ("b", _defer), ("postal", postal_resolver)],
    )
    assert r.channel == CHANNEL_POSTAL


def test_waterfall_returns_none_only_for_unusable_name():
    """Even the postal floor declines a blank name → the lone None outcome."""
    assert resolve_contact("", "Zaragoza") is None


def test_waterfall_isolates_a_raising_resolver():
    """A provider that throws must be skipped, not bubble up and kill the run."""
    def _boom(name, locality, **kw):
        raise RuntimeError("provider down")

    r = resolve_contact(
        "María Pérez", "Zaragoza",
        resolvers=[("boom", _boom), ("postal", postal_resolver)],
    )
    assert r.channel == CHANNEL_POSTAL  # fell through the exception to postal


def test_default_chain_order_is_stub_stub_notary_postal():
    """Lock the documented priority so a reorder is a conscious change."""
    assert [n for n, _ in cr.DEFAULT_RESOLVERS] == ["skiptrace", "registry", "notaria", "postal"]


# ── resolve_for_lead (lead-dict mapping) ────────────────────────────────────

def test_resolve_for_lead_prefers_heir_name():
    lead = {"heir_name": "Ana Heredera", "causante": "Juan Difunto", "localidad": "Huesca"}
    r = resolve_for_lead(lead)
    assert "Ana Heredera" in r.target  # heir, not causante, is the living target


def test_resolve_for_lead_falls_back_to_causante():
    lead = {"heir_name": "", "causante": "Juan Difunto", "localidad": "Huesca"}
    r = resolve_for_lead(lead)
    assert "Juan Difunto" in r.target


def test_resolve_for_lead_uses_office_for_notarial():
    lead = {
        "causante": "Juan Difunto", "localidad": "Zaragoza",
        "juzgado": "Notaría de María Ruiz de Zaragoza",
    }
    r = resolve_for_lead(lead)
    assert r.channel == CHANNEL_NOTARY
    assert r.target == "Notaría de María Ruiz de Zaragoza"


def test_resolve_for_lead_passes_street_address_through():
    lead = {"heir_name": "Ana Heredera", "localidad": "Zaragoza", "direccion": "Av. Goya 10"}
    r = resolve_for_lead(lead)
    assert r.confidence == CONF_HIGH
    assert "Av. Goya 10" in r.target


def test_resolve_for_lead_returns_none_without_a_name():
    assert resolve_for_lead({"localidad": "Zaragoza"}) is None


# ── ContactResolver Protocol ────────────────────────────────────────────────

def test_bare_functions_satisfy_the_protocol():
    """runtime_checkable Protocol: the registered resolvers are valid instances."""
    assert isinstance(postal_resolver, ContactResolver)
    assert isinstance(notary_resolver, ContactResolver)
    for _name, fn in cr.DEFAULT_RESOLVERS:
        assert isinstance(fn, ContactResolver)
