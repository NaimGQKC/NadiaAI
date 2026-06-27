"""Address extraction regression tests.

The old parser captured only letters, so it dropped the house NUMBER and every
edict address was unresolvable in Catastro (which requires a number). These lock
in that the street + number survive and the trailing boilerplate is stripped.
"""

import re

from nadia_ai.utils.edict_parse import (
    _extract_domicile,
    _extract_finca,
    parse_notaria_edict,
    parse_teju_edict,
)


def test_keeps_street_and_number_drops_protocol_noise():
    t = ("Declaración de herederos ab intestato de don Juan Pérez, con último "
         "domicilio en Calle Mayor 23, Zaragoza, número 145 de mi protocolo.")
    assert _extract_domicile(t) == "Calle Mayor 23, Zaragoza"
    assert re.search(r"\d", _extract_domicile(t))  # Catastro-resolvable


def test_prefers_domicilio_marker_over_vecino():
    t = ("Declaración de herederos de doña María Ruiz, vecina de Huesca con "
         "domicilio en C/ del Coso, 14, 2º B. a fin de que comparezcan.")
    # Must pick the street after "con domicilio en", not "Huesca".
    assert _extract_domicile(t) == "C/ del Coso, 14, 2º B"


def test_domiciliado_marker():
    t = "Declaración de herederos de don Luis, domiciliado en Avenida de Aragon 5 donde residía."
    assert _extract_domicile(t) == "Avenida de Aragon 5"


def test_city_only_is_clean_and_not_resolvable():
    t = "Declaración de herederos ab intestato de don Pedro Gil, con último domicilio en Madrid."
    out = _extract_domicile(t)
    assert out == "Madrid"
    assert not re.search(r"\d", out)  # no number → correctly skipped by Catastro


def test_no_domicile_returns_empty():
    assert _extract_domicile("Texto del edicto sin domicilio alguno.") == ""


def test_parse_notaria_edict_exposes_domicile():
    t = ("NOTARÍA DE ZARAGOZA. Declaración de herederos ab intestato de don Juan "
         "Pérez García, con último domicilio en Calle Mayor 23, Zaragoza, número "
         "145 de mi protocolo.")
    assert parse_notaria_edict(t)["domicile"] == "Calle Mayor 23, Zaragoza"


# ── Judicial (BOE-TEJU) finca extraction — the lever: these used to extract 0 ──


def test_finca_sita_en_with_number():
    t = ("Herencia yacente de don Antonio Gil. La finca sita en Calle Coso 14, "
         "Zaragoza, inscrita en el Registro de la Propiedad número 3.")
    assert _extract_finca(t) == "Calle Coso 14, Zaragoza"
    assert re.search(r"\d", _extract_finca(t))


def test_inmueble_sito_en():
    t = "Se hace saber que el inmueble sito en Avenida de Goya 45, 2º, propiedad de la herencia."
    assert _extract_finca(t) == "Avenida de Goya 45, 2º"


def test_finca_registral_numbered():
    t = ("Procedimiento contra la herencia yacente. La finca registral número "
         "12345 sita en Calle San Miguel 8 de esta ciudad.")
    assert _extract_finca(t) == "Calle San Miguel 8"


def test_finca_returns_empty_when_absent():
    t = "Edicto judicial de herencia yacente sin mención de inmueble alguno."
    assert _extract_finca(t) == ""


def test_parse_teju_edict_exposes_finca():
    t = ("ÓRGANO JUDICIAL Tribunal de Instancia de Zaragoza Plaza n. La vivienda "
         "sita en Paseo Independencia 12, Zaragoza, donde residía el causante.")
    assert parse_teju_edict(t)["finca"] == "Paseo Independencia 12, Zaragoza"
