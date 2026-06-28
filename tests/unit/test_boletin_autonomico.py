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
        recs = ba.scrape_boletin(ba.DOGV_CONFIG)  # search mode
    # Same link returned for every keyword query → deduped to one record.
    assert len(recs) == 1
    assert recs[0].source == "dogv"


def test_scrape_boletin_never_raises_on_network_error():
    with mock.patch.object(ba, "_get", side_effect=Exception("boom")):
        # Engine must swallow errors and return [] so the pipeline never breaks.
        assert ba.scrape_boletin(ba.BOCM_CONFIG) == []


def test_extract_numbers_most_recent_first():
    import re
    html = (
        '<a href="/eboja/2026/12/index.html">12</a>'
        '<a href="/eboja/2026/120/index.html">120</a>'
        '<a href="/eboja/2026/12/index.html">12 dup</a>'
    )
    nums = ba._extract_numbers(html, re.compile(r"/eboja/\d{4}/(\d+)/"))
    assert nums == [120, 12]  # unique, descending


def test_boja_index_crawl_hits_section4(monkeypatch):
    # Year index lists boletín numbers; each boletín's /s4 section has an edict.
    index_html = (
        '<a href="/eboja/2026/120/index.html">BOJA 120</a>'
        '<a href="/eboja/2026/119/index.html">BOJA 119</a>'
    )
    s4_html = (
        '<a href="/boja/2026/120/54.html">Edicto. Declaración de herederos '
        'abintestato de Don Pedro Ruiz Mora</a>'
    )
    seen_urls = []

    def fake_get(url):
        seen_urls.append(url)
        return index_html if "/eboja/2026.html" in url else s4_html

    monkeypatch.setattr(ba, "_get", fake_get)
    recs = ba.scrape_boja()
    # It must request the section-4 URL, not the raw sumario index.
    assert any("/boja/2026/120/s4" in u for u in seen_urls)
    assert recs and all(r.source == "boja" for r in recs)
    assert any(r.causante == "Pedro Ruiz Mora" for r in recs)


def test_new_configs_registered():
    assert ba.BOJA_CONFIG["mode"] == "index_crawl"
    assert ba.DOGV_CONFIG["source"] == "dogv"
    # scrape_dogv uses search mode and is fail-safe.
    with mock.patch.object(ba, "_get", return_value=None):
        assert ba.scrape_dogv() == []


def test_find_results_list_unknown_shape():
    assert ba._find_results_list({"results": [{"id": 1}]}) == [{"id": 1}]
    # nested + alternative key
    assert ba._find_results_list({"response": {"rows": [{"x": 2}]}}) == [{"x": 2}]
    # first list-of-dicts fallback
    assert ba._find_results_list({"foo": [{"a": 1}]}) == [{"a": 1}]
    assert ba._find_results_list({"n": 0, "items": []}) == []


def test_doc_url_from_id_and_url():
    cfg = {"doc_url": "https://x.es/doc/?documentId={id}"}
    assert ba._doc_url_from({"documentId": "987"}, cfg) == "https://x.es/doc/?documentId=987"
    assert ba._doc_url_from({"link": "https://x.es/a"}, cfg) == "https://x.es/a"
    assert ba._doc_url_from({"nope": 1}, cfg) is None


def test_dogc_json_api_builds_records(monkeypatch):
    fake = {"numResults": 1, "results": [
        {"documentId": "987654", "titol": "EDICTE sobre l'herència jacent de Pere Soler"}
    ]}
    monkeypatch.setattr(ba, "_post_json", lambda url, body: fake)
    recs = ba.scrape_dogc()
    assert recs and recs[0].source == "dogc"
    assert recs[0].source_url == "https://dogc.gencat.cat/ca/document-del-dogc/?documentId=987654"


def test_dogc_json_api_fail_safe(monkeypatch):
    monkeypatch.setattr(ba, "_post_json", lambda url, body: None)
    assert ba.scrape_dogc() == []
