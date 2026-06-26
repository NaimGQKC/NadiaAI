"""Heir sanitization regression tests.

Locks in the rule the client flagged: an heir must never be the deceased
(causante) or the handling notary/court — those are nonsensical and were a
common error in earlier runs.
"""

from unittest import mock

import nadia_ai.utils.extraction as ex


def _extract(fake_llm, causante_hint):
    with mock.patch.object(ex, "_extract_via_llm", return_value=fake_llm):
        return ex.extract_inheritance_data("texto del edicto", causante_hint=causante_hint)


def test_deceased_is_dropped_from_heirs():
    fake = {
        "deceased_name": "JUAN PÉREZ GARCÍA",
        "list_of_heirs": ["Juan Perez Garcia", "María López Pérez"],
        "property_address": None,
        "referencia_catastral": None,
    }
    out = _extract(fake, causante_hint="Juan Pérez García")
    # Accent/case/space-folded match → the deceased echo is removed.
    assert "María López Pérez" in out["list_of_heirs"]
    assert all("perez garcia" != h.lower().replace("é", "e").replace("í", "i")
               for h in out["list_of_heirs"])


def test_notary_and_court_are_dropped_from_heirs():
    fake = {
        "deceased_name": "Ana Ruiz",
        "list_of_heirs": ["Notaría de Zaragoza", "Juzgado de Primera Instancia", "Pedro Ruiz"],
        "property_address": None,
        "referencia_catastral": None,
    }
    out = _extract(fake, causante_hint="Ana Ruiz")
    assert out["list_of_heirs"] == ["Pedro Ruiz"]


def test_real_family_sharing_surnames_is_kept():
    # Children share surnames with the deceased — they must NOT be dropped.
    fake = {
        "deceased_name": "Juan Pérez García",
        "list_of_heirs": ["María Pérez García", "Pedro Pérez García"],
        "property_address": None,
        "referencia_catastral": None,
    }
    out = _extract(fake, causante_hint="Juan Pérez García")
    assert set(out["list_of_heirs"]) == {"María Pérez García", "Pedro Pérez García"}
