"""Tests for deterministic BOE edict field extraction.

Fixtures are verbatim (accents intact) excerpts of real BOE-TEJU and BOE-N edicts
the pipeline fetched, trimmed to the parsed region.
"""

from nadia_ai.utils.edict_parse import (
    _days_from_plazo,
    parse_notaria_edict,
    parse_teju_edict,
)

# Real TEJU edict (lead 4553) that names two interested parties alongside the
# "herencia yacente de <causante>" label.
TEJU_WITH_PARTIES = (
    "ÓRGANO JUDICIAL Sección Civil y de Instrucción del Tribunal de Instancia de "
    "Durango. Plaza nº 1 Dirección: Plaza Ezkurdi Localidad: Durango Código Postal: "
    "48200 Provincia: Bizkaia Teléfono: 946030051 Correo electrónico: "
    "mixto1.durango@justizia.eus PROCEDIMIENTO Civil Número: 1000042/2026 NIG: "
    "4802742120220001377 RESOLUCIÓN PROCESAL Decreto Fecha: 20/05/2026 Objeto de la "
    "publicación edictal: Notificación. Plazo: cinco días DESTINATARIOS ANA BELEN "
    "ANDRADE NEVES - NIF/NIE: ***9636** HERENCIA YACENTE DE ABILIO ANDRADE ALVES Y DE "
    "MARIA FERNANDA NEVES MACEDO JOSE MANUEL ANDRADE NEVES - NIF/NIE: ***7738** Se "
    "hace saber que, para conocer íntegramente la resolución..."
)

# Real TEJU edict (lead 4456): only the estate itself is addressed — no living heir.
TEJU_ESTATE_ONLY = (
    "ÓRGANO JUDICIAL Sección Civil y de Instrucción del Tribunal de Instancia de "
    "Almería. Dirección: Carretera RONDA Localidad: Almería Provincia: Almería "
    "Teléfono: 950809013 Correo electrónico: civil.almeria@justicia.es PROCEDIMIENTO "
    "Civil Número: 433/2025 RESOLUCIÓN PROCESAL Decreto Objeto de la publicación "
    "edictal: Notificación. Plazo: 20 días DESTINATARIOS HERENCIA YACENTE JUAN MANUEL "
    "LAZARO MARTINEZ - NIF/NIE: ***3137** Se hace saber que..."
)

# Real BOE-N notarial edict (lead 5615) whose causante landed empty in the DB.
NOTARIA = (
    "NOTARÍA DE FRANCISCO JAVIER VALVERDE FERNÁNDEZ DE CORIA DEL RIO. Anuncio de "
    "notificación de 20 de mayo de 2026 en procedimiento Declaración de herederos ab "
    "intestato de don Jose Manuel Nuñez Jimenez, vecino de Coria del Río. ID: "
    "N2600392369 Se comunica a las personas ignoradas... que por su hermana doña Maria "
    "Angeles Nuñez Jimenez se está tramitando Acta de Notoriedad con el número 1128 de "
    "mi protocolo de 2026... su último domicilio en Coria del Río (Sevilla)... En el "
    "plazo de veinte días... en mi despacho profesional sito en Calle Pinta, 16, Bajo, "
    "de Coria del Río, a fin de oponerse..."
)


class TestParseTeju:
    def test_court_phone_email_procedure(self):
        d = parse_teju_edict(TEJU_WITH_PARTIES)
        assert "Tribunal de Instancia de Durango" in d["court"]
        assert d["phone"] == "946030051"
        assert d["email"] == "mixto1.durango@justizia.eus"
        assert d["procedure"] == "1000042/2026"
        assert d["plazo_days"] == 5

    def test_named_parties_extracted_not_the_causante_label(self):
        d = parse_teju_edict(TEJU_WITH_PARTIES)
        # The two living interested parties are heirs; the "HERENCIA YACENTE DE …"
        # names (the causantes) must NOT leak into parties.
        assert "Ana Belen Andrade Neves" in d["parties"]
        assert "Jose Manuel Andrade Neves" in d["parties"]
        assert not any("Abilio" in p or "Fernanda" in p for p in d["parties"])

    def test_estate_only_yields_no_living_party(self):
        d = parse_teju_edict(TEJU_ESTATE_ONLY)
        assert d["parties"] == []
        # The estate's own name is recovered as the causante, not a heir.
        assert d["causante"] == "Juan Manuel Lazaro Martinez"
        assert d["phone"] == "950809013"

    def test_empty_text(self):
        d = parse_teju_edict("")
        assert d["court"] == "" and d["parties"] == [] and d["plazo_days"] is None


class TestParseNotaria:
    def test_fills_causante_heir_protocol_domicile(self):
        d = parse_notaria_edict(NOTARIA)
        assert d["causante"] == "Jose Manuel Nuñez Jimenez"
        assert d["heir"] == "Maria Angeles Nuñez Jimenez"
        assert d["protocol"] == "1128"
        assert "Coria del Río" in d["domicile"]
        assert "VALVERDE" in d["notary"].upper()
        assert "Calle Pinta" in d["office"]


class TestPlazo:
    def test_words_and_digits(self):
        assert _days_from_plazo("veinte días") == 20
        assert _days_from_plazo("5 días") == 5
        assert _days_from_plazo("cinco días") == 5
        assert _days_from_plazo("un mes") == 30
        assert _days_from_plazo("") is None
