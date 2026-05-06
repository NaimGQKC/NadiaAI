"""Tests for BOA scraper — JSON API based."""

import responses

from nadia_ai.scrapers.boa import (
    RC_PATTERN,
    RC_PATTERN_SHORT,
    NAME_FROM_TITLE,
    NAME_FROM_TEXT,
    JUZGADO_PATTERN,
    NOTARIA_PATTERN,
    fetch_boa_json,
    parse_boa_record,
    scrape_boa,
)


class TestBOAExtractRC:
    def test_standard_format(self):
        m = RC_PATTERN.search("referencia catastral 9872301TF6697S0001WX")
        assert m is not None
        assert m.group(1).upper() == "9872301TF6697S0001WX"

    def test_labeled_format(self):
        m = RC_PATTERN_SHORT.search("ref. catastral: 9872301TF6697S0001WX")
        assert m is not None

    def test_no_match(self):
        assert RC_PATTERN.search("no property reference here") is None


class TestBOANameExtraction:
    def test_title_sucesion_legal_a_favor(self):
        title = (
            "ORDEN HAP/1052/2025, por la que se acuerda el inicio de "
            "procedimiento de sucesion legal de D. Emilio Alejandro Anadon Diaus "
            "a favor de la Comunidad Autonoma de Aragon."
        )
        m = NAME_FROM_TITLE.search(title)
        assert m is not None
        assert "Emilio" in m.group(1)
        assert "Comunidad" not in m.group(1)

    def test_title_sucesion_legal_por_la(self):
        title = (
            "ORDEN por la que se acuerda la sucesion legal de D. Joaquin Conesa Gomez "
            "por la Comunidad Autonoma de Aragon."
        )
        m = NAME_FROM_TITLE.search(title)
        assert m is not None
        assert "Joaquin" in m.group(1)
        assert "Comunidad" not in m.group(1)

    def test_body_causante_with_honorific(self):
        text = "herencia del causante Don García López María, fallecido en enero"
        m = NAME_FROM_TEXT.search(text)
        assert m is not None

    def test_body_fallecida_with_honorific(self):
        text = "la fallecida Doña María Pérez Gómez, natural de Zaragoza"
        m = NAME_FROM_TEXT.search(text)
        assert m is not None

    def test_body_rejects_noise_without_honorific(self):
        text = "herencia de la citada causante, residente en Zaragoza"
        m = NAME_FROM_TEXT.search(text)
        # Should not match — no D./Doña honorific
        assert m is None


class TestJuzgadoExtraction:
    def test_juzgado_primera_instancia_numbered(self):
        text = "ante el Juzgado de Primera Instancia n.º 5 de Zaragoza se tramita"
        m = JUZGADO_PATTERN.search(text)
        assert m is not None
        assert "Primera Instancia" in m.group(1)
        assert "5" in m.group(1)
        assert "Zaragoza" in m.group(1)

    def test_juzgado_instruccion(self):
        text = "Juzgado de Primera Instancia e Instrucción de Ejea de los Caballeros"
        m = JUZGADO_PATTERN.search(text)
        assert m is not None
        assert "Instrucción" in m.group(1)

    def test_notaria(self):
        text = "según acta de la Notaría de D. Pedro García López, de Zaragoza"
        m = NOTARIA_PATTERN.search(text)
        assert m is not None
        assert "Pedro" in m.group(1)

    def test_parse_record_extracts_juzgado(self):
        raw = {
            "Titulo": "Declaración de herederos abintestato",
            "Texto": (
                "Se tramita en el Juzgado de Primera Instancia n.º 3 de Zaragoza "
                "la declaración de herederos del causante D. Juan García López."
            ),
            "FechaPublicacion": "20260415",
            "DOCN": "doc-juz-test",
        }
        record = parse_boa_record(raw)
        assert record is not None
        assert record.juzgado is not None
        assert "Primera Instancia" in record.juzgado
        assert "3" in record.juzgado


class TestParseBOARecord:
    def test_full_record(self):
        raw = {
            "Titulo": (
                "ORDEN por la que se acuerda el inicio de procedimiento "
                "de sucesion legal de D. Juan Garcia Lopez a favor de la "
                "Comunidad Autonoma de Aragon."
            ),
            "Texto": "El causante D. Juan Garcia Lopez falleció en Zaragoza.",
            "FechaPublicacion": "20260415",
            "DOCN": "BOA-e-2026-1234",
            "UrlPdf": "https://boa.aragon.es/test.pdf",
        }
        record = parse_boa_record(raw)
        assert record is not None
        assert record.source == "boa"
        assert record.causante is not None
        assert "Juan" in record.causante
        assert record.published_at is not None
        assert record.published_at.year == 2026

    def test_record_with_rc_in_text(self):
        raw = {
            "Titulo": "Junta Distribuidora de Herencias",
            "Texto": "Finca con referencia catastral 9872301TF6697S0001WX en Zaragoza.",
            "FechaPublicacion": "20260101",
            "DOCN": "doc123",
        }
        record = parse_boa_record(raw)
        assert record is not None
        assert record.referencia_catastral == "9872301TF6697S0001WX"

    def test_empty_record(self):
        record = parse_boa_record({})
        assert record is None

    def test_record_without_name(self):
        raw = {
            "Titulo": "Junta Distribuidora de Herencias — sesión ordinaria",
            "FechaPublicacion": "20260101",
            "DOCN": "doc456",
        }
        record = parse_boa_record(raw)
        assert record is not None
        assert record.causante is None


class TestFetchBOAJson:
    @responses.activate
    def test_successful_fetch(self):
        responses.add(
            responses.GET,
            "https://www.boa.aragon.es/cgi-bin/EBOA/BRSCGI",
            json=[
                {"Titulo": "Test 1", "DOCN": "1", "FechaPublicacion": "20260101"},
                {"Titulo": "Test 2", "DOCN": "2", "FechaPublicacion": "20260102"},
            ],
            status=200,
        )
        results = fetch_boa_json("test query")
        assert len(results) == 2

    @responses.activate
    def test_empty_results(self):
        responses.add(
            responses.GET,
            "https://www.boa.aragon.es/cgi-bin/EBOA/BRSCGI",
            json=[],
            status=200,
        )
        results = fetch_boa_json("nothing")
        assert results == []


class TestScrapeBOA:
    @responses.activate
    def test_scrape_deduplicates(self):
        """Same record from different queries should be deduplicated."""
        responses.add(
            responses.GET,
            "https://www.boa.aragon.es/cgi-bin/EBOA/BRSCGI",
            json=[{"Titulo": "Sucesion legal de D. Test", "DOCN": "same-doc", "FechaPublicacion": "20260101"}],
            status=200,
        )
        records = scrape_boa()
        # Even though 3 queries run, same DOCN should appear once
        source_ids = [r.source_id for r in records]
        assert len(source_ids) == len(set(source_ids))

    @responses.activate
    def test_scrape_handles_error(self):
        responses.add(
            responses.GET,
            "https://www.boa.aragon.es/cgi-bin/EBOA/BRSCGI",
            status=500,
        )
        records = scrape_boa()
        assert records == []
