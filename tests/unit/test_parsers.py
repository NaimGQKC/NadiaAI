"""Tests for scraper parsers — Tablón JSON API and BOA."""

import responses

from nadia_ai.scrapers.boa import RC_PATTERN as boa_rc_pattern
from nadia_ai.scrapers.tablon import (
    extract_name_from_title,
    fetch_herederos_edicts,
    parse_api_record,
    scrape_tablon,
)


class TestExtractNameFromTitle:
    def test_standard_title(self):
        title = (
            "Acta notoriedad para declaracion herederos abintestato, "
            "por el fallecimiento de DON GARCIA LOPEZ MARIA"
        )
        name = extract_name_from_title(title)
        assert name is not None
        assert "Garcia Lopez Maria" in name

    def test_dona_title(self):
        title = "Declaracion herederos por fallecimiento de DOÑA PEREZ SANCHEZ ANA"
        name = extract_name_from_title(title)
        assert name is not None
        assert "Perez" in name

    def test_no_match(self):
        title = "Licencia de obras para CL COSO 15"
        name = extract_name_from_title(title)
        assert name is None

    def test_title_with_comma_after_name(self):
        title = "Acta por fallecimiento de DON MARTINEZ RUIZ PEDRO, notaría de Zaragoza"
        name = extract_name_from_title(title)
        assert name is not None
        assert "Martinez Ruiz Pedro" in name

    def test_herederos_abintestato_de_dona(self):
        title = "Acta de declaracion herederos abintestato de DOÑA JOAQUINA ORTIZ GARCES"
        name = extract_name_from_title(title)
        assert name is not None
        assert "Joaquina" in name
        assert "Ortiz" in name

    def test_herencia_yacente(self):
        title = "Herencia Yacen de D. Ramon Jose Chueca Alonso"
        name = extract_name_from_title(title)
        assert name is not None
        assert "Ramon" in name
        assert "Chueca" in name

    def test_acta_notoriedad_don(self):
        title = "Acta de notoriedad para declaracion herederos, DON BENITO ARTAL ARTAL"
        name = extract_name_from_title(title)
        assert name is not None
        assert "Benito" in name


class TestParseAPIRecord:
    def test_full_record(self):
        raw = {
            "id": "4150",
            "title": "Acta notoriedad por fallecimiento de DON GARCIA LOPEZ",
            "author": "NOTARIO: PEREZ GOMEZ JUAN",
            "publicationDate": "2026-04-15T00:10:01",
            "expirationDate": "2026-05-15T00:00:00",
            "status": 3,
            "tipo": {"id": 29, "title": "Acta de notoriedad/Declaracion de herederos"},
            "document": "https://www.zaragoza.es/sede/servicio/tablon-edicto/4150/document",
        }
        record = parse_api_record(raw)
        assert record.source == "tablon"
        assert record.source_id == "4150"
        assert record.causante is not None
        assert "Garcia Lopez" in record.causante
        assert record.published_at is not None
        assert record.published_at.year == 2026
        assert record.referencia_catastral is None  # Not in tablon data

    def test_minimal_record(self):
        raw = {"id": "100", "title": "Unknown edict type"}
        record = parse_api_record(raw)
        assert record.source_id == "100"
        assert record.causante is None

    def test_date_parsing_with_timezone(self):
        raw = {
            "id": "200",
            "title": "Test",
            "publicationDate": "2026-04-15T00:10:01Z",
        }
        record = parse_api_record(raw)
        assert record.published_at is not None


class TestFetchHerederosEdicts:
    @responses.activate
    def test_successful_fetch(self):
        # fetch_herederos_edicts now runs 2 queries (tipo-based + text-based)
        responses.add(
            responses.GET,
            "https://www.zaragoza.es/sede/servicio/tablon-edicto.json",
            json={
                "totalCount": 2,
                "start": 0,
                "rows": 200,
                "result": [
                    {"id": 4150, "title": "Test 1"},
                    {"id": 4151, "title": "Test 2"},
                ],
            },
            status=200,
        )
        responses.add(
            responses.GET,
            "https://www.zaragoza.es/sede/servicio/tablon-edicto.json",
            json={"totalCount": 0, "result": []},
            status=200,
        )
        results = fetch_herederos_edicts()
        assert len(results) == 2

    @responses.activate
    def test_empty_results(self):
        responses.add(
            responses.GET,
            "https://www.zaragoza.es/sede/servicio/tablon-edicto.json",
            json={"totalCount": 0, "start": 0, "rows": 200, "result": []},
            status=200,
        )
        responses.add(
            responses.GET,
            "https://www.zaragoza.es/sede/servicio/tablon-edicto.json",
            json={"totalCount": 0, "result": []},
            status=200,
        )
        results = fetch_herederos_edicts()
        assert results == []


class TestScrapeTablon:
    @responses.activate
    def test_scrape_returns_records(self):
        responses.add(
            responses.GET,
            "https://www.zaragoza.es/sede/servicio/tablon-edicto.json",
            json={
                "totalCount": 1,
                "result": [
                    {
                        "id": 4150,
                        "title": "Acta por fallecimiento de DON GARCIA LOPEZ",
                        "publicationDate": "2026-04-15T00:10:01",
                        "document": "https://example.com/doc",
                    }
                ],
            },
        )
        responses.add(
            responses.GET,
            "https://www.zaragoza.es/sede/servicio/tablon-edicto.json",
            json={"totalCount": 0, "result": []},
        )
        records = scrape_tablon()
        assert len(records) == 1
        assert records[0].source == "tablon"
        assert records[0].causante is not None

    @responses.activate
    def test_scrape_handles_api_error(self):
        # Both queries fail — should return empty
        responses.add(
            responses.GET,
            "https://www.zaragoza.es/sede/servicio/tablon-edicto.json",
            status=500,
        )
        responses.add(
            responses.GET,
            "https://www.zaragoza.es/sede/servicio/tablon-edicto.json",
            status=500,
        )
        records = scrape_tablon()
        assert records == []


class TestBOAExtractRC:
    def test_same_patterns_as_tablon(self):
        text = "referencia catastral 9872301TF6697S0001WX"
        m = boa_rc_pattern.search(text)
        assert m is not None
