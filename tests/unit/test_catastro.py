"""Tests for Catastro client."""

import responses

from nadia_ai.catastro import _infer_neighborhood, _parse_response, lookup_by_rc

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
