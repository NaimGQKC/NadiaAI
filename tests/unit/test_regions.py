"""Province → comunidad autónoma resolution tests."""

from nadia_ai.utils.regions import UNKNOWN, catastro_province, ccaa_for


class TestCatastroProvince:
    def test_postal_code_is_authoritative(self):
        # First 2 digits of the CP = province code, canonical Catastro name.
        assert catastro_province("Calle Mayor 5, 50001 Zaragoza") == "ZARAGOZA"
        assert catastro_province("C/ X 3, 08010 Barcelona") == "BARCELONA"
        assert catastro_province("Rua Y 2, 15001 A Coruña") == "A CORUÑA"

    def test_region_province_name_canonicalized(self):
        # Obituary leads store region = province; map to the Catastro spelling
        # (traditional Castilian — co-official forms are tried as retry alternates).
        assert catastro_province("Calle X 4", region="Coruna") == "A CORUÑA"
        assert catastro_province("Calle X 4", region="Gerona") == "GERONA"
        assert catastro_province("Calle X 4", region="Vizcaya") == "VIZCAYA"
        assert catastro_province("Calle X 4", region="Bizkaia") == "VIZCAYA"

    def test_province_alternates(self):
        from nadia_ai.utils.regions import province_alternates
        assert "BIZKAIA" in province_alternates("VIZCAYA")
        assert "GIRONA" in province_alternates("GERONA")
        assert province_alternates("MADRID") == []

    def test_city_fallback(self):
        assert catastro_province("Calle X 4", localidad="Calatayud") == "ZARAGOZA"
        assert catastro_province("Calle X 4", localidad="Dos Hermanas") == "SEVILLA"

    def test_ccaa_region_is_not_a_province(self):
        # BOE leads store region = CCAA ("Andalucía"); that is NOT a province, so
        # without a CP/city we return None rather than send Catastro garbage.
        assert catastro_province("Calle X 4", region="Andalucía") is None

    def test_unknown_returns_none(self):
        assert catastro_province("Calle X 4", region="", localidad="") is None


def test_province_maps_to_ccaa():
    assert ccaa_for("Zaragoza") == "Aragón"
    assert ccaa_for("Barcelona") == "Cataluña"
    assert ccaa_for("Madrid") == "Madrid"
    assert ccaa_for("Vizcaya") == "País Vasco"


def test_slug_and_accent_variants():
    assert ccaa_for("la-rioja") == "La Rioja"
    assert ccaa_for("coruna") == "Galicia"
    assert ccaa_for("CASTELLÓN") == "Comunidad Valenciana"


def test_region_already_holding_ccaa():
    # data_repair sets some rows' region to the CCAA name directly.
    assert ccaa_for("Aragón") == "Aragón"


def test_falls_through_candidates_then_unknown():
    # region empty → use localidad; nothing recognizable → UNKNOWN.
    assert ccaa_for("", "Teruel") == "Aragón"
    assert ccaa_for(None, None) == UNKNOWN
    assert ccaa_for("Lisboa") == UNKNOWN


def test_substring_match_in_address_tail():
    assert ccaa_for("Calle Mayor 5, 50001 Zaragoza") == "Aragón"
