"""Tests for the Heraldo de Aragón esquelas scraper (offline parse logic)."""

from datetime import UTC, datetime

from nadia_ai.scrapers.heraldo import (
    _detail_links,
    _parse_detail,
    parse_family_heirs,
)

_SAMPLE = """<html><head><title>Carmen Alejandre Sabio | Esquela Heraldo de Aragón</title></head>
<body><h1>Carmen Alejandre Sabio</h1>
<p>Falleció en Zaragoza el día 13 de junio de 2026. Habiendo recibido los Santos
Sacramentos. D.E.P. Sus apenados: esposo, don José Javier Gil Barranco; hijas,
Virginia y Ángela; nietos, Valero y Carla.</p></body></html>"""


def test_parse_detail_extracts_name_city_date():
    d = _parse_detail(_SAMPLE, "dona-carmen-alejandre-sabio")
    assert d is not None
    assert d["name"] == "Carmen Alejandre Sabio"
    assert d["localidad"] == "Zaragoza"
    assert d["dod"] == datetime(2026, 6, 13, tzinfo=UTC)


def test_parse_detail_handles_other_city_and_no_dia():
    html = ('<title>Don Juan Manso | Esquela Heraldo de Aragón</title>'
            'Falleció en Madrid el 15 de mayo de 2026 D.E.P. Sus apenados: esposa, doña Elena.')
    d = _parse_detail(html, "don-juan-manso")
    # Leading courtesy title is stripped so the causante field is a clean name.
    assert d["name"] == "Juan Manso"
    assert d["localidad"] == "Madrid"
    assert d["dod"] == datetime(2026, 5, 15, tzinfo=UTC)


def test_parse_detail_strips_dona_honorific():
    html = ('<title>Doña Carmen Alejandre Sabio | Esquela Heraldo de Aragón</title>'
            'Falleció en Zaragoza el día 13 de junio de 2026.')
    d = _parse_detail(html, "dona-carmen-alejandre-sabio")
    assert d["name"] == "Carmen Alejandre Sabio"


def test_parse_detail_rejects_non_person_title():
    html = "<title>Esquelas | Heraldo de Aragón</title>Falleció en Zaragoza el 1 de enero de 2026"
    # "Esquelas" alone is not a valid person name -> None
    assert _parse_detail(html, "esquelas") is None


def test_parse_detail_includes_family_heirs():
    d = _parse_detail(_SAMPLE, "dona-carmen-alejandre-sabio")
    # The "Sus apenados:" block is parsed into heirs and carried on the record.
    assert "José Javier Gil Barranco" in d["heirs"]


def test_detail_links_dedupes_and_filters():
    html = ('<a href="/esquelas/florentino-gabas-gracia.html">x</a>'
            '<a href="/esquelas/florentino-gabas-gracia.html">dup</a>'
            '<a href="/esquelas/">listing</a>'
            '<a href="/esquelas/dona-carmen-alejandre-sabio.html">y</a>')
    links = _detail_links(html)
    assert links == [
        "/esquelas/florentino-gabas-gracia.html",
        "/esquelas/dona-carmen-alejandre-sabio.html",
    ]


# ── parse_family_heirs ──────────────────────────────────────────────────────

# Format 1: "esposo ...; hijas, Virginia y Ángela; nietos ...; hermana ..."
# Mixes a full-name spouse, first-name-only children, and a sibling. Honorifics
# ("don"/"doña") prefix the full names and must be stripped.
_APENADOS_SPOUSE_CHILDREN = (
    "Carmen Alejandre Sabio. Falleció en Zaragoza el día 13 de junio de 2026. "
    "Sus apenados: esposo, don José Javier Gil Barranco; hijas, Virginia y Ángela; "
    "nietos, Valero y Carla; hermana, Olga Paula Alejandre Sabio; "
    "sobrinos, primos y demás familia. D.E.P. El funeral se celebrará mañana."
)

# Format 2: a siblings-led notice, "hermanos, José María y María Paz, María ...".
_APENADOS_SIBLINGS = (
    "Don Florentino Gabás Gracia. Falleció en Huesca el 2 de junio de 2026. "
    "Sus apenados: hermanos, José María Gabás Gracia y María Paz Gabás Gracia, "
    "María Pilar Gabás Gracia; sobrinos y demás familia. Q.E.P.D."
)

# The family block as it arrives inside an escaped JSON-LD "description" value:
# unicode escapes (á) and "\r\n" survive the page fetch.
_JSONLD_PAGE = (
    '<script type="application/ld+json">{"@type":"Person",'
    '"name":"Mar\\u00eda Pilar Lab\\u00e9n",'
    '"description":"Falleci\\u00f3 en Zaragoza.\\r\\n'
    'Sus apenados: esposo, don Antonio Calvo Sanz; hijos, '
    'Roberto Calvo Lab\\u00e9n y Sof\\u00eda Calvo Lab\\u00e9n; '
    'hermanos, Jes\\u00fas y Ana. D.E.P."}</script>'
)


def test_parse_family_heirs_spouse_children_format():
    heirs = parse_family_heirs(_APENADOS_SPOUSE_CHILDREN)
    # Full-name relatives come back validated, with the honorific stripped.
    assert "José Javier Gil Barranco" in heirs
    assert "Olga Paula Alejandre Sabio" in heirs
    # The kinship labels and trailing "demás familia" boilerplate are not names.
    assert not any("familia" in h.lower() for h in heirs)
    assert "esposo" not in [h.lower() for h in heirs]


def test_parse_family_heirs_spouse_ordered_before_other_relatives():
    heirs = parse_family_heirs(_APENADOS_SPOUSE_CHILDREN)
    assert heirs, "expected at least the spouse"
    # Spouse/children relations are emitted before siblings/others.
    spouse = "José Javier Gil Barranco"
    sibling = "Olga Paula Alejandre Sabio"
    assert spouse in heirs and sibling in heirs
    assert heirs.index(spouse) < heirs.index(sibling)


def test_parse_family_heirs_siblings_format():
    heirs = parse_family_heirs(_APENADOS_SIBLINGS)
    # Names split on both "," and " y " within the siblings relation.
    assert "José María Gabás Gracia" in heirs
    assert "María Paz Gabás Gracia" in heirs
    assert "María Pilar Gabás Gracia" in heirs


def test_parse_family_heirs_reads_escaped_jsonld_description():
    heirs = parse_family_heirs(_JSONLD_PAGE)
    # Unicode escapes (\u00xx) are decoded without corrupting the UTF-8 accents,
    # and the spouse + children are recovered from the escaped description.
    assert "Antonio Calvo Sanz" in heirs
    assert "Roberto Calvo Labén" in heirs
    assert "Sofía Calvo Labén" in heirs
    # Spouse/children (primary relations) precede the siblings relation.
    assert heirs.index("Antonio Calvo Sanz") < heirs.index("Roberto Calvo Labén")


def test_parse_family_heirs_validated_names_only():
    heirs = parse_family_heirs(_APENADOS_SPOUSE_CHILDREN)
    from nadia_ai.utils.names import is_valid_person_name
    # Every returned heir passes the mandatory project validator.
    assert heirs
    assert all(is_valid_person_name(h) for h in heirs)


def test_parse_family_heirs_block_ends_at_sentinel():
    # Funeral text after the sentinel must not leak in as names.
    heirs = parse_family_heirs(_APENADOS_SPOUSE_CHILDREN)
    assert not any("funeral" in h.lower() for h in heirs)


def test_parse_family_heirs_no_block_returns_empty():
    html = ("<title>Carmen Alejandre Sabio | Esquela Heraldo de Aragón</title>"
            "Falleció en Zaragoza el día 13 de junio de 2026. D.E.P.")
    assert parse_family_heirs(html) == []


def test_parse_family_heirs_empty_input_returns_empty():
    assert parse_family_heirs("") == []
    assert parse_family_heirs("   ") == []
