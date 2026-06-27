"""Tests for the generic autonomous-community bulletin scraper (BOCM/DOGC)."""

from unittest import mock

import nadia_ai.scrapers.boletin_autonomico as ba


def test_extract_edict_links_keeps_only_inheritance():
    html = """
    <ul>
      <li><a href="/doc/1">Edicto. Declaración de herederos abintestato de Don Juan Pérez García</a></li>
      <li><a href="/doc/2">Anuncio de licitación de obra pública</a></li>
      <li><a href="https://x.es/doc/3">Herencia yacente de Doña María López</a></li>
    </ul>
    """
    links = ba._extract_edict_links(html, "https://www.bocm.es")
    urls = [u for u, _ in links]
    assert "https://www.bocm.es/doc/1" in urls       # relative joined to base
    assert "https://x.es/doc/3" in urls              # absolute kept
    assert all("licitaci" not in t.lower() for _, t in links)  # non-edict dropped
    assert len(links) == 2


def test_causante_from_title():
    assert ba._causante_from_title(
        "Declaración de herederos abintestato de Don Juan Pérez García"
    ) == "Juan Pérez García"
    assert ba._causante_from_title("Herencia yacente de Doña María López Ruiz") == "María López Ruiz"
    assert ba._causante_from_title("Anuncio de obra pública") is None


def test_scrape_boletin_builds_records_and_is_safe():
    html = '<a href="/e/1">Herencia yacente de Don Pedro Gil Soria</a>'
    with mock.patch.object(ba, "_get", return_value=html):
        recs = ba.scrape_boletin(ba.BOCM_CONFIG)
    assert recs, "should produce at least one record"
    r = recs[0]
    assert r.source == "bocm"
    assert r.source_url == "https://www.bocm.es/e/1"
    assert r.causante == "Pedro Gil Soria"
    assert r.edict_type == "declaracion_herederos_abintestato"


def test_scrape_boletin_dedupes_across_queries():
    html = '<a href="/e/1">Declaración de herederos abintestato de Don Luis Marín</a>'
    with mock.patch.object(ba, "_get", return_value=html):
        recs = ba.scrape_boletin(ba.DOGC_CONFIG)
    # Same link returned for every keyword query → deduped to one record.
    assert len(recs) == 1
    assert recs[0].source == "dogc"


def test_scrape_boletin_never_raises_on_network_error():
    with mock.patch.object(ba, "_get", side_effect=Exception("boom")):
        # Engine must swallow errors and return [] so the pipeline never breaks.
        assert ba.scrape_boletin(ba.BOCM_CONFIG) == []


def test_find_links_matches_boletin_pattern():
    import re
    html = (
        '<a href="/eboja/2026/12/index.html">BOJA 12</a>'
        '<a href="/eboja/2026/13/index.html">BOJA 13</a>'
        '<a href="/eboja/sobre-boja/info.html">Sobre BOJA</a>'
    )
    links = ba._find_links(html, re.compile(r"/eboja/\d{4}/\d+/(?:index\.html)?$"),
                           "https://www.juntadeandalucia.es")
    assert "https://www.juntadeandalucia.es/eboja/2026/12/index.html" in links
    assert len(links) == 2  # the "sobre-boja" link is excluded


def test_boja_index_crawl_mode(monkeypatch):
    # Year index lists 2 boletines; each sumario has one inheritance edict.
    index_html = (
        '<a href="/eboja/2026/12/index.html">BOJA 12</a>'
        '<a href="/eboja/2026/13/index.html">BOJA 13</a>'
    )
    sumario_html = (
        '<a href="/eboja/2026/12/4.html">Edicto. Declaración de herederos '
        'abintestato de Don Pedro Ruiz Mora</a>'
    )

    def fake_get(url):
        return index_html if url.endswith(".html") and "/eboja/2026.html" in url else sumario_html

    monkeypatch.setattr(ba, "_get", fake_get)
    recs = ba.scrape_boja()
    assert recs and all(r.source == "boja" for r in recs)
    assert any(r.causante == "Pedro Ruiz Mora" for r in recs)


def test_new_configs_registered():
    assert ba.BOJA_CONFIG["mode"] == "index_crawl"
    assert ba.DOGV_CONFIG["source"] == "dogv"
    # scrape_dogv uses search mode and is fail-safe.
    with mock.patch.object(ba, "_get", return_value=None):
        assert ba.scrape_dogv() == []
