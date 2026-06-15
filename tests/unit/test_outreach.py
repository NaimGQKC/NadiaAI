"""Tests for the AI-outreach layer (deterministic path — no network)."""

from nadia_ai.outreach import lead_type, render_outreach, _fmt_price


def test_lead_type_routing():
    assert lead_type({"source": "fsbo_pisos"}) == "fsbo"
    assert lead_type({"subsource": "BOE-N"}) == "notarial"
    assert lead_type({"subsource": "BOA-JD"}) == "notarial"
    assert lead_type({"subsource": "BOE-TEJU"}) == "judicial"
    assert lead_type({"subsource": "Defunciones"}) == "obituary"
    assert lead_type({"sources": '["Heraldo"]'}) == "obituary"
    assert lead_type({}) == "obituary"  # cautious default


def test_fmt_price():
    assert _fmt_price(222500) == "222.500 €"
    assert _fmt_price(None) == "el precio indicado"
    assert _fmt_price("x") == "el precio indicado"


def _fully_rendered_has_no_leftover_placeholders(text: str) -> bool:
    # No unfilled {placeholder} should remain in customer-facing copy.
    import re

    return not re.search(r"\{[a-z_]+\}", text)


def test_fsbo_outreach_is_a_call_with_filled_fields():
    lead = {"source": "fsbo_pisos", "address": "Delicias", "price_eur": 222500,
            "localidad": "zaragoza_capital", "phone": "876 754 067", "listing_id": "999"}
    o = render_outreach(lead)
    assert o["tipo"].startswith("FSBO")
    assert o["canal"] == "Llamada + WhatsApp"
    assert o["guion_llamada"]            # FSBO gets a call script
    assert "Delicias" in o["guion_llamada"]
    assert "222.500 €" in o["guion_llamada"]
    assert o["mensaje"]                  # and a WhatsApp message
    assert o["asunto"] == ""             # no letter subject for a call
    assert _fully_rendered_has_no_leftover_placeholders(o["guion_llamada"])
    assert _fully_rendered_has_no_leftover_placeholders(o["mensaje"])


def test_notarial_outreach_is_a_respectful_letter_naming_the_causante():
    lead = {"subsource": "BOE-N", "causante": "Carmen Alejandre Sabio",
            "localidad": "Zaragoza", "juzgado": "Notaría De Dámaso Cruz", "id": 1}
    o = render_outreach(lead)
    assert o["canal"] == "Carta postal"
    assert o["guion_llamada"] == ""               # never cold-call inheritance
    assert "Carmen Alejandre Sabio" in o["mensaje"]
    assert o["asunto"]                             # has a letter subject
    assert "RGPD" in o["mensaje"]                  # data-source transparency
    assert _fully_rendered_has_no_leftover_placeholders(o["mensaje"])


def test_obituary_outreach_is_gentle_and_delayed():
    lead = {"sources": '["Heraldo"]', "causante": "Marcelino Montesa Seral",
            "localidad": "Zaragoza", "heir_names_json": '["Jose Maria", "Maria Paz"]', "id": 2}
    o = render_outreach(lead)
    assert "esperar" in o["canal"].lower()
    assert o["guion_llamada"] == ""               # no hot call to the bereaved
    assert "condolencias" in o["mensaje"].lower()
    assert "Marcelino Montesa Seral" in o["mensaje"]
    assert "Jose Maria" in o["mensaje"]           # familia filled from heirs
    assert _fully_rendered_has_no_leftover_placeholders(o["mensaje"])


def test_render_is_deterministic_without_llm():
    lead = {"subsource": "BOE-N", "causante": "Test Persona", "localidad": "Zaragoza", "id": 3}
    assert render_outreach(lead) == render_outreach(lead)
