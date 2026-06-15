"""Tests for the Zaragoza Tablón de Edictos name extraction (offline)."""

from nadia_ai.scrapers.tablon import extract_name_from_title


def test_extracts_and_titlecases_allcaps_name():
    title = "Edicto por declaracion de herederos abintestato de DOÑA JOAQUINA ORTIZ GARCES"
    assert extract_name_from_title(title) == "Joaquina Ortiz Garces"


def test_strips_feminine_ordinal_honorific():
    # "Dª" ordinal form must be stripped AND the remainder title-cased.
    title = "Declaracion de herederos de Dª MARIA CARMEN JASA PUEYO"
    assert extract_name_from_title(title) == "Maria Carmen Jasa Pueyo"


def test_rejects_bare_honorific_letter():
    # Titles that yield just "D" (no real name) must be rejected, not stored.
    assert extract_name_from_title("Acta de notoriedad de D.") is None


def test_rejects_boilerplate_non_name():
    assert extract_name_from_title("Anuncio sobre el procedimiento administrativo") is None
