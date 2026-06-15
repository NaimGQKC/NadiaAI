"""Tests for the pisos.com FSBO scraper (offline parse logic).

These tests MUST NOT hit the network. The fixture below is a representative
``.ad-preview`` card captured live from
https://www.pisos.com/venta/pisos-zaragoza_capital/de_particulares/ on
2026-06-14 and trimmed to the elements the parser reads (title link, price,
subtitle, char chips). A second card exercises dedup + missing fields.
"""

from nadia_ai.scrapers.fsbo import (
    _parse_card,
    _parse_page,
    _parse_price,
    _split_location,
    scrape_fsbo,
)
from bs4 import BeautifulSoup

# Representative two-card results page (real pisos.com structure, trimmed).
_SAMPLE_PAGE = """
<html><body>
<div class="grid">
  <div class="ad-preview" data-lnk-href="/comprar/chalet-delicias_distrito-62514121277_108500/">
    <a class="ad-preview__title" href="/comprar/chalet-delicias_distrito-62514121277_108500/">Chalet en Delicias</a>
    <div class="ad-preview__subtitle">Delicias (Zaragoza Capital)</div>
    <span class="ad-preview__price">222.500 €</span>
    <div class="ad-preview__char">4 habs.</div>
    <div class="ad-preview__char">1 baño</div>
    <div class="ad-preview__char">106 m²</div>
  </div>
  <div class="ad-preview" data-lnk-href="/comprar/piso-zaragoza_capital_centro50008-35890816334_100500/">
    <a class="ad-preview__title" href="/comprar/piso-zaragoza_capital_centro50008-35890816334_100500/">Piso en Centro</a>
    <div class="ad-preview__subtitle">Centro (Zaragoza Capital)</div>
    <span class="ad-preview__price">145.000 €</span>
    <div class="ad-preview__char">2 habs.</div>
    <div class="ad-preview__char">78 m²</div>
  </div>
</div>
</body></html>
"""

# A sparse card: no price ("A consultar"), no chars — must still yield a row with
# a usable listing_url but price_eur=None.
_SPARSE_CARD = """
<div class="ad-preview" data-lnk-href="/comprar/piso-las_fuentes-99999999999_555000/">
  <a class="ad-preview__title" href="/comprar/piso-las_fuentes-99999999999_555000/">Piso en Las Fuentes</a>
  <div class="ad-preview__subtitle">Las Fuentes</div>
  <span class="ad-preview__price">A consultar</span>
</div>
"""


def _card(html: str):
    return BeautifulSoup(html, "html.parser").select_one(".ad-preview")


def test_parse_card_extracts_all_fields():
    lead = _parse_card(_card(_SAMPLE_PAGE), "zaragoza_capital", "2026-06-14")
    assert lead is not None
    assert lead["source"] == "fsbo_pisos"
    assert lead["title"] == "Chalet en Delicias"
    assert lead["price_eur"] == 222500
    assert lead["m2"] == 106
    assert lead["rooms"] == 4
    assert lead["localidad"] == "zaragoza_capital"
    assert lead["provincia"] == "Zaragoza Capital"
    assert lead["address"] == "Delicias"
    assert lead["listing_id"] == "62514121277"
    assert lead["listing_url"].startswith("https://www.pisos.com/comprar/")
    assert lead["anunciante"] == "particular"
    assert lead["scraped_at"] == "2026-06-14"


def test_parse_page_returns_listings_with_price_and_https_url():
    leads = _parse_page(_SAMPLE_PAGE, "zaragoza_capital", "2026-06-14")
    # The core contract: >=1 listing, with a non-None price and an https URL.
    assert len(leads) >= 1
    priced = [x for x in leads if x["price_eur"] is not None]
    assert priced, "expected at least one listing with a parsed price"
    assert all(x["listing_url"].startswith("https://") for x in leads)
    assert priced[0]["price_eur"] == 222500


def test_sparse_card_keeps_url_but_null_price():
    lead = _parse_card(_card(_SPARSE_CARD), "zaragoza_capital", "2026-06-14")
    assert lead is not None
    assert lead["price_eur"] is None  # "A consultar" -> no number
    assert lead["listing_url"].startswith("https://")
    assert lead["m2"] is None and lead["rooms"] is None


def test_parse_price_handles_thousands_and_junk():
    assert _parse_price("222.500 €") == 222500
    assert _parse_price("1.250.000 €") == 1250000
    assert _parse_price("A consultar") is None
    assert _parse_price(None) is None


def test_split_location():
    assert _split_location("Delicias (Zaragoza Capital)") == ("Delicias", "Zaragoza Capital")
    assert _split_location("Las Fuentes") == ("Las Fuentes", None)
    assert _split_location("") == (None, None)


def test_scrape_fsbo_dedupes_by_id(monkeypatch):
    """scrape_fsbo must dedup repeated listing ids and never hit the network."""
    pages = iter([_SAMPLE_PAGE, _SAMPLE_PAGE])  # page 2 duplicates page 1

    class _Resp:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

    def _fake_get(url, *a, **k):
        return _Resp(next(pages, ""))

    monkeypatch.setattr("nadia_ai.scrapers.fsbo.SESSION.get", _fake_get)
    monkeypatch.setattr("nadia_ai.scrapers.fsbo.time.sleep", lambda *_: None)

    leads = scrape_fsbo(["zaragoza_capital"], max_per_loc=60, enrich_phones=False)
    ids = [x["listing_id"] for x in leads]
    assert len(ids) == len(set(ids)) == 2  # two unique cards, duplicates dropped


def test_extract_detail_phone_prefers_owner_over_switchboard():
    """The owner's number must win; pisos.com's switchboard must be excluded."""
    from nadia_ai.scrapers.fsbo import extract_detail_phone

    # Owner's landline in a telefono context -> grouped and returned.
    assert extract_detail_phone('foo "telefono":"876754067" bar') == "876 754 067"
    # Mobile in a "phone" context.
    assert extract_detail_phone('{"phone":"686010942"}') == "686 010 942"
    # Switchboard alone -> None (not the seller).
    assert extract_detail_phone('"telefono":"976200648"') is None
    # Switchboard first, owner second -> owner wins.
    assert extract_detail_phone('"telefono":"976200648" ... "telefono":"686010942"') == "686 010 942"
    # No phone present -> None.
    assert extract_detail_phone("<html>no number here</html>") is None
