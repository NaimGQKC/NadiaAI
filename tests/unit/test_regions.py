"""Province → comunidad autónoma resolution tests."""

from nadia_ai.utils.regions import UNKNOWN, ccaa_for


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
