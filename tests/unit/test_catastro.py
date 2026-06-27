"""Tests for Catastro client."""

import responses

from nadia_ai.catastro import (
    _parse_address,
    _parse_rc_from_loc,
    _city_from_address,
    _infer_neighborhood,
    _parse_response,
    _PROVINCIA_BY_CITY,
    lookup_by_rc,
)


class TestParseRcFromLoc:
    def test_ok_returns_rc_and_reason(self):
        xml = ("<consulta_dnploc><coordenadas><coord><pc><pc1>9872301</pc1>"
               "<pc2>TF6697S</pc2></pc></coord></coordenadas></consulta_dnploc>")
        assert _parse_rc_from_loc(xml) == ("9872301TF6697S", "ok")

    def test_error_surfaces_catastro_message(self):
        xml = ("<consulta_dnploc><control><cuerr>1</cuerr></control>"
               "<lerr><err><cod>43</cod><des>LA VIA NO EXISTE</des></err></lerr>"
               "</consulta_dnploc>")
        rc, reason = _parse_rc_from_loc(xml)
        assert rc is None
        assert "VIA NO EXISTE" in reason

    def test_malformed_xml(self):
        assert _parse_rc_from_loc("not xml") == (None, "parse-error")


class TestAddressParse:
    def test_calle_with_number(self):
        assert _parse_address("Calle Mayor 23, Zaragoza") == ("CL", "MAYOR", "23")

    def test_abbreviated_via_and_numero_word(self):
        # "C/" → CL and the word "número" must be stripped from the house number.
        assert _parse_address("C/ del Coso, número 14") == ("CL", "COSO", "14")

    def test_avenida_strips_article(self):
        # The leading article ("de") is stripped; accents are folded later, in
        # lookup_rc_by_address via strip_accents, so the name keeps its accent here.
        assert _parse_address("Avenida de Aragón 5") == ("AV", "ARAGÓN", "5")

    def test_no_number_unresolvable(self):
        assert _parse_address("Calle Mayor, Zaragoza") is None


class TestProvinceGeography:
    def test_aragon_town_maps_to_real_province(self):
        # The 1/77 bug: non-capital towns mapped city==province and always failed.
        assert _PROVINCIA_BY_CITY["calatayud"] == "ZARAGOZA"
        assert _PROVINCIA_BY_CITY["monzon"] == "HUESCA"
        assert _PROVINCIA_BY_CITY["alcaniz"] == "TERUEL"

    def test_city_from_address_prefers_longest(self):
        assert _city_from_address("Calle X 5, Ejea de los Caballeros") == "Ejea De Los Caballeros"

SAMPLE_CATASTRO_XML = """<?xml version="1.0" encoding="utf-8"?>
<consulta_dnp>
  <control>
    <cudnp>1</cudnp>
  </control>
  <bico>
    <bi>
      <idbi>
        <rc>
          <pc1>9872301</pc1>
          <pc2>TF6697S</pc2>
          <car>0001</car>
          <cc1>W</cc1>
          <cc2>X</cc2>
        </rc>
      </idbi>
      <dt>
        <locs>
          <lous>
            <lourb>
              <dir>
                <tv>CL</tv>
                <nv>COSO</nv>
                <pnp>15</pnp>
              </dir>
              <loint>
                <es>1</es>
                <pt>1</pt>
                <pu>A</pu>
              </loint>
            </lourb>
          </lous>
        </locs>
      </dt>
      <ldt>CL COSO 15 Es:1 Pl:1 Pt:A 50003 ZARAGOZA (ZARAGOZA)</ldt>
      <debi>
        <luso>Residencial</luso>
        <sfc>85</sfc>
        <ant>1960</ant>
      </debi>
    </bi>
  </bico>
</consulta_dnp>
"""


class TestParseResponse:
    def test_parse_full_response(self):
        info = _parse_response("9872301TF6697S0001WX", SAMPLE_CATASTRO_XML)
        assert info is not None
        assert info.referencia_catastral == "9872301TF6697S0001WX"
        assert "COSO" in info.address
        assert info.m2 == 85.0
        assert info.year_built == 1960
        assert info.use_class == "Residencial"

    def test_parse_empty_response(self):
        xml = (
            '<?xml version="1.0"?><consulta_dnp><control><cudnp>0</cudnp></control></consulta_dnp>'
        )
        info = _parse_response("TEST123", xml)
        assert info is None

    def test_parse_malformed_xml(self):
        info = _parse_response("TEST123", "not xml at all")
        assert info is None


class TestInferNeighborhood:
    def test_casco_historico(self):
        assert _infer_neighborhood("CL COSO 15, Casco Histórico") == "Casco Histórico"

    def test_delicias(self):
        assert _infer_neighborhood("AV NAVARRA 50, DELICIAS") == "Delicias"

    def test_unknown(self):
        assert _infer_neighborhood("CL INVENTADA 99") == ""


class TestLookupByRC:
    def test_invalid_rc_too_short(self):
        result = lookup_by_rc("SHORT")
        assert result is None

    @responses.activate
    def test_successful_lookup(self):
        responses.add(
            responses.GET,
            "https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx/Consulta_DNPRC",
            body=SAMPLE_CATASTRO_XML,
            status=200,
            content_type="application/xml",
        )
        result = lookup_by_rc("9872301TF6697S0001WX")
        assert result is not None
        assert result.m2 == 85.0

    @responses.activate
    def test_404_returns_none(self):
        responses.add(
            responses.GET,
            "https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx/Consulta_DNPRC",
            status=404,
        )
        result = lookup_by_rc("9872301TF6697S0001WX")
        assert result is None
