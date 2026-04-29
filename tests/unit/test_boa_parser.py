"""Tests for BOA scraper parser."""

from nadia_ai.scrapers.boa import (
    extract_ref_catastral,
    parse_boa_document,
    parse_boa_search_results,
)


class TestBOAExtractRC:
    def test_standard_format(self):
        rc = extract_ref_catastral("referencia catastral 9872301TF6697S0001WX")
        assert rc == "9872301TF6697S0001WX"

    def test_labeled_format(self):
        rc = extract_ref_catastral("ref. catastral: 9872301TF6697S0001WX")
        assert rc is not None

    def test_no_match(self):
        assert extract_ref_catastral("no property reference here") is None


class TestParseBOADocument:
    def test_empty_html(self):
        records = parse_boa_document("<html><body></body></html>")
        assert records == []

    def test_document_with_rc(self):
        html = """
        <html><body>
        <h1>Junta Distribuidora de Herencias</h1>
        <p>Inventario de bienes de la herencia del causante Don García López,
        fallecido el 15 de enero de 2026.</p>
        <p>Finca urbana con referencia catastral 9872301TF6697S0001WX,
        sita en Calle Coso 15, Zaragoza.</p>
        </body></html>
        """
        records = parse_boa_document(html, source_url="https://boa.aragon.es/test")
        assert len(records) == 1
        assert records[0].source == "boa"
        assert records[0].referencia_catastral == "9872301TF6697S0001WX"

    def test_document_with_multiple_rcs(self):
        html = """
        <html><body>
        <p>Herencia: finca 9872301TF6697S0001WX y finca 1234567AB1234C0001DE</p>
        </body></html>
        """
        records = parse_boa_document(html)
        assert len(records) == 2
        rcs = {r.referencia_catastral for r in records}
        assert "9872301TF6697S0001WX" in rcs
        assert "1234567AB1234C0001DE" in rcs

    def test_date_extraction_spanish(self):
        html = """
        <html><body>
        <p>Publicado el 15 de marzo de 2026.
        Referencia catastral 9872301TF6697S0001WX.</p>
        </body></html>
        """
        records = parse_boa_document(html)
        assert len(records) == 1
        assert records[0].published_at is not None
        assert records[0].published_at.month == 3
        assert records[0].published_at.day == 15

    def test_causante_extraction(self):
        html = """
        <html><body>
        <p>Herencia de causante García López María.
        Referencia catastral 9872301TF6697S0001WX.</p>
        </body></html>
        """
        records = parse_boa_document(html)
        assert len(records) == 1
        assert records[0].causante is not None


class TestParseBOASearchResults:
    def test_extract_document_urls(self):
        html = """
        <html><body>
        <a href="/cgi-bin/EBOA/BRSCGI?CMD=VEROBJ&doc=123">Doc 1</a>
        <a href="/other/link">Not relevant</a>
        <a href="/cgi-bin/EBOA/BRSCGI?CMD=VEROBJ&doc=456">Doc 2</a>
        </body></html>
        """
        urls = parse_boa_search_results(html)
        assert len(urls) == 2
        assert all("EBOA" in u or "BRSCGI" in u for u in urls)

    def test_empty_results(self):
        html = "<html><body><p>No results</p></body></html>"
        urls = parse_boa_search_results(html)
        assert urls == []
