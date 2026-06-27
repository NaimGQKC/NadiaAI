"""Province → comunidad autónoma resolution.

Leads are tagged per *provincia* (the product is sold per-province to local
agents), but the commercial question "where do we already have enough leads to
sell?" is answered better at the *comunidad autónoma* level. This maps every
Spanish province (in the accent/slug variants the scrapers actually store —
"zaragoza", "Coruna", "la-rioja"…) to its CCAA, and tolerates a `region` that
already holds the CCAA name.
"""

from __future__ import annotations

from nadia_ai.merge import strip_accents


def _norm(s: str | None) -> str:
    """Fold a province/region string to a lookup key: no accents, lowercase,
    hyphens/underscores → spaces, collapsed whitespace."""
    if not s:
        return ""
    s = strip_accents(str(s)).lower().replace("-", " ").replace("_", " ")
    return " ".join(s.split())


# Province (normalized) → CCAA display name. Includes common spelling/slug and
# co-official (Basque/Catalan/Galician) variants the scrapers may emit.
_PROVINCE_TO_CCAA = {
    # Andalucía
    "almeria": "Andalucía", "cadiz": "Andalucía", "cordoba": "Andalucía",
    "granada": "Andalucía", "huelva": "Andalucía", "jaen": "Andalucía",
    "malaga": "Andalucía", "sevilla": "Andalucía",
    # Aragón
    "huesca": "Aragón", "teruel": "Aragón", "zaragoza": "Aragón",
    # Principado de Asturias
    "asturias": "Asturias", "oviedo": "Asturias",
    # Illes Balears
    "baleares": "Baleares", "illes balears": "Baleares", "islas baleares": "Baleares",
    # Canarias
    "las palmas": "Canarias", "tenerife": "Canarias",
    "santa cruz de tenerife": "Canarias",
    # Cantabria
    "cantabria": "Cantabria", "santander": "Cantabria",
    # Castilla-La Mancha
    "albacete": "Castilla-La Mancha", "ciudad real": "Castilla-La Mancha",
    "cuenca": "Castilla-La Mancha", "guadalajara": "Castilla-La Mancha",
    "toledo": "Castilla-La Mancha",
    # Castilla y León
    "avila": "Castilla y León", "burgos": "Castilla y León", "leon": "Castilla y León",
    "palencia": "Castilla y León", "salamanca": "Castilla y León",
    "segovia": "Castilla y León", "soria": "Castilla y León",
    "valladolid": "Castilla y León", "zamora": "Castilla y León",
    # Cataluña
    "barcelona": "Cataluña", "gerona": "Cataluña", "girona": "Cataluña",
    "lerida": "Cataluña", "lleida": "Cataluña", "tarragona": "Cataluña",
    # Comunidad Valenciana
    "alicante": "Comunidad Valenciana", "alacant": "Comunidad Valenciana",
    "castellon": "Comunidad Valenciana", "castello": "Comunidad Valenciana",
    "valencia": "Comunidad Valenciana",
    # Extremadura
    "badajoz": "Extremadura", "caceres": "Extremadura",
    # Galicia
    "coruna": "Galicia", "a coruna": "Galicia", "la coruna": "Galicia",
    "lugo": "Galicia", "orense": "Galicia", "ourense": "Galicia",
    "pontevedra": "Galicia",
    # Comunidad de Madrid
    "madrid": "Madrid",
    # Región de Murcia
    "murcia": "Murcia",
    # Comunidad Foral de Navarra
    "navarra": "Navarra", "pamplona": "Navarra",
    # País Vasco
    "alava": "País Vasco", "araba": "País Vasco", "guipuzcoa": "País Vasco",
    "gipuzkoa": "País Vasco", "vizcaya": "País Vasco", "bizkaia": "País Vasco",
    "bilbao": "País Vasco", "san sebastian": "País Vasco",
    # La Rioja
    "la rioja": "La Rioja", "rioja": "La Rioja", "logrono": "La Rioja",
    # Ciudades autónomas
    "ceuta": "Ceuta", "melilla": "Melilla",
}

# CCAA names mapped to themselves, so a `region` that already holds the CCAA
# ("Aragón" from data_repair) resolves without falling through to "Otra/Desconocida".
_CCAA_SELF = {_norm(v): v for v in set(_PROVINCE_TO_CCAA.values())}

UNKNOWN = "Otra/Desconocida"


def ccaa_for(*candidates: str | None) -> str:
    """Resolve a comunidad autónoma from any of the lead's location strings
    (try region first, then localidad…). Returns UNKNOWN if none match."""
    for c in candidates:
        key = _norm(c)
        if not key:
            continue
        if key in _PROVINCE_TO_CCAA:
            return _PROVINCE_TO_CCAA[key]
        if key in _CCAA_SELF:
            return _CCAA_SELF[key]
        # Substring fallback: "calle x, 50001 zaragoza" → match a known province.
        for prov, ccaa in _PROVINCE_TO_CCAA.items():
            if prov in key:
                return ccaa
    return UNKNOWN
