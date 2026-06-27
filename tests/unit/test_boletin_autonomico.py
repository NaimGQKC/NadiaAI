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
