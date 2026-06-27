"""Province / comunidad-autónoma resolution.

Two jobs, one source of truth:

1. **Territory** (`ccaa_for`): which comunidad autónoma a lead belongs to, for the
   commercial "where can we already sell?" view. Leads are tagged per provincia
   (obituary scrapers) OR per CCAA (BOE `extract_region`) OR "Desconocida", so we
   resolve from any of region / localidad / address, falling back to the postal
   code in the address.

2. **Catastro** (`catastro_province`): the *canonical* province name the Catastro
   callejero (Consulta_DNPLOC) accepts. Its `Provincia` field is strict and uses
   specific spellings ("A CORUÑA", "GIRONA", "ARABA/ALAVA", "STA. CRUZ DE
   TENERIFE"). The dominant Catastro failure was "LA PROVINCIA NO EXISTE" (55% of
   misses) because we were sending the city name as the province. The most
   reliable key is the **postal code** (first 2 digits = province code), then a
   known province/region name, then a capital-city lookup.
"""

from __future__ import annotations

import re

from nadia_ai.merge import strip_accents


def _norm(s: str | None) -> str:
    """Fold a string to a lookup key: no accents, lowercase, hyphens/underscores
    → spaces, collapsed whitespace (slashes/periods kept for canonical names)."""
    if not s:
        return ""
    s = strip_accents(str(s)).lower().replace("-", " ").replace("_", " ")
    return " ".join(s.split())


# Postal-code 2-digit prefix → canonical Catastro province name. Authoritative
# whenever the address carries a CP, and complete (all 52 provinces).
_CP2_TO_PROVINCE = {
    "01": "ARABA/ALAVA", "02": "ALBACETE", "03": "ALICANTE", "04": "ALMERIA",
    "05": "AVILA", "06": "BADAJOZ", "07": "ILLES BALEARS", "08": "BARCELONA",
    "09": "BURGOS", "10": "CACERES", "11": "CADIZ", "12": "CASTELLON",
    "13": "CIUDAD REAL", "14": "CORDOBA", "15": "A CORUÑA", "16": "CUENCA",
    "17": "GIRONA", "18": "GRANADA", "19": "GUADALAJARA", "20": "GIPUZKOA",
    "21": "HUELVA", "22": "HUESCA", "23": "JAEN", "24": "LEON", "25": "LLEIDA",
    "26": "LA RIOJA", "27": "LUGO", "28": "MADRID", "29": "MALAGA", "30": "MURCIA",
    "31": "NAVARRA", "32": "OURENSE", "33": "ASTURIAS", "34": "PALENCIA",
    "35": "LAS PALMAS", "36": "PONTEVEDRA", "37": "SALAMANCA",
    "38": "STA. CRUZ DE TENERIFE", "39": "CANTABRIA", "40": "SEGOVIA",
    "41": "SEVILLA", "42": "SORIA", "43": "TARRAGONA", "44": "TERUEL",
    "45": "TOLEDO", "46": "VALENCIA", "47": "VALLADOLID", "48": "BIZKAIA",
    "49": "ZAMORA", "50": "ZARAGOZA", "51": "CEUTA", "52": "MELILLA",
}

# Canonical province → comunidad autónoma.
_PROVINCE_CCAA = {
    "ARABA/ALAVA": "País Vasco", "GIPUZKOA": "País Vasco", "BIZKAIA": "País Vasco",
    "ALBACETE": "Castilla-La Mancha", "CIUDAD REAL": "Castilla-La Mancha",
    "CUENCA": "Castilla-La Mancha", "GUADALAJARA": "Castilla-La Mancha",
    "TOLEDO": "Castilla-La Mancha",
    "ALICANTE": "Comunidad Valenciana", "CASTELLON": "Comunidad Valenciana",
    "VALENCIA": "Comunidad Valenciana",
    "ALMERIA": "Andalucía", "CADIZ": "Andalucía", "CORDOBA": "Andalucía",
    "GRANADA": "Andalucía", "HUELVA": "Andalucía", "JAEN": "Andalucía",
    "MALAGA": "Andalucía", "SEVILLA": "Andalucía",
    "AVILA": "Castilla y León", "BURGOS": "Castilla y León", "LEON": "Castilla y León",
    "PALENCIA": "Castilla y León", "SALAMANCA": "Castilla y León",
    "SEGOVIA": "Castilla y León", "SORIA": "Castilla y León",
    "VALLADOLID": "Castilla y León", "ZAMORA": "Castilla y León",
    "BADAJOZ": "Extremadura", "CACERES": "Extremadura",
    "ILLES BALEARS": "Baleares",
    "BARCELONA": "Cataluña", "GIRONA": "Cataluña", "LLEIDA": "Cataluña",
    "TARRAGONA": "Cataluña",
    "A CORUÑA": "Galicia", "LUGO": "Galicia", "OURENSE": "Galicia",
    "PONTEVEDRA": "Galicia",
    "CANTABRIA": "Cantabria", "LA RIOJA": "La Rioja", "MADRID": "Madrid",
    "MURCIA": "Murcia", "NAVARRA": "Navarra", "ASTURIAS": "Asturias",
    "LAS PALMAS": "Canarias", "STA. CRUZ DE TENERIFE": "Canarias",
    "HUESCA": "Aragón", "TERUEL": "Aragón", "ZARAGOZA": "Aragón",
    "CEUTA": "Ceuta", "MELILLA": "Melilla",
}

# Any common/slug/canonical spelling (normalized) → canonical Catastro province.
_TO_CANONICAL = {_norm(p): p for p in _PROVINCE_CCAA}
_TO_CANONICAL.update({
    _norm(k): v for k, v in {
        # Galician / Catalan / Basque official vs. common Castilian spellings.
        "coruna": "A CORUÑA", "a coruna": "A CORUÑA", "la coruna": "A CORUÑA",
        "gerona": "GIRONA", "lerida": "LLEIDA", "orense": "OURENSE",
        "alava": "ARABA/ALAVA", "araba": "ARABA/ALAVA",
        "guipuzcoa": "GIPUZKOA", "vizcaya": "BIZKAIA",
        "baleares": "ILLES BALEARS", "islas baleares": "ILLES BALEARS",
        "illes balears": "ILLES BALEARS",
        "tenerife": "STA. CRUZ DE TENERIFE",
        "santa cruz de tenerife": "STA. CRUZ DE TENERIFE",
        "rioja": "LA RIOJA",
        # Capital cities whose name ≠ province name.
        "bilbao": "BIZKAIA", "san sebastian": "GIPUZKOA", "donostia": "GIPUZKOA",
        "vitoria": "ARABA/ALAVA", "vitoria gasteiz": "ARABA/ALAVA",
        "pamplona": "NAVARRA", "iruna": "NAVARRA", "santander": "CANTABRIA",
        "logrono": "LA RIOJA", "oviedo": "ASTURIAS", "gijon": "ASTURIAS",
        "palma": "ILLES BALEARS", "palma de mallorca": "ILLES BALEARS",
    }.items()
})

# Major non-capital cities → canonical province, for leads with only a city.
_CITY_TO_PROVINCE = {_norm(k): v for k, v in {
    # Aragón (home market) and a spread of large Spanish cities.
    "calatayud": "ZARAGOZA", "utebo": "ZARAGOZA", "ejea de los caballeros": "ZARAGOZA",
    "tarazona": "ZARAGOZA", "caspe": "ZARAGOZA", "monzon": "HUESCA",
    "barbastro": "HUESCA", "jaca": "HUESCA", "fraga": "HUESCA", "alcaniz": "TERUEL",
    "vigo": "PONTEVEDRA", "marbella": "MALAGA", "jerez": "CADIZ",
    "jerez de la frontera": "CADIZ", "cartagena": "MURCIA", "elche": "ALICANTE",
    "alcala de henares": "MADRID", "mostoles": "MADRID", "fuenlabrada": "MADRID",
    "leganes": "MADRID", "getafe": "MADRID", "alcorcon": "MADRID",
    "dos hermanas": "SEVILLA", "sabadell": "BARCELONA", "terrassa": "BARCELONA",
    "badalona": "BARCELONA", "hospitalet": "BARCELONA",
    "l hospitalet de llobregat": "BARCELONA", "santiago": "A CORUÑA",
    "santiago de compostela": "A CORUÑA", "ferrol": "A CORUÑA", "gandia": "VALENCIA",
    "torrevieja": "ALICANTE", "benidorm": "ALICANTE", "algeciras": "CADIZ",
}.items()}

UNKNOWN = "Otra/Desconocida"
_CP_RE = re.compile(r"\b(\d{2})\d{3}\b")


def _province_from(direccion: str | None, region: str | None,
                   localidad: str | None) -> str | None:
    """Canonical Catastro province from the best available signal, or None.

    Priority: postal code (authoritative) → region/localidad as a known province
    or capital → known non-capital city. Returns None when nothing is recognized
    (callers should skip rather than send Catastro a bogus province)."""
    m = _CP_RE.search(direccion or "")
    if m and m.group(1) in _CP2_TO_PROVINCE:
        return _CP2_TO_PROVINCE[m.group(1)]
    for cand in (region, localidad):
        key = _norm(cand)
        if key in _TO_CANONICAL:
            return _TO_CANONICAL[key]
        if key in _CITY_TO_PROVINCE:
            return _CITY_TO_PROVINCE[key]
    # Substring fallback: a province/city name embedded in the address tail.
    tail = _norm(direccion)
    if tail:
        for key, prov in _TO_CANONICAL.items():
            if key in tail:
                return prov
    return None


def catastro_province(direccion: str | None, region: str | None = None,
                      localidad: str | None = None) -> str | None:
    """Canonical province name accepted by Catastro DNPLOC, or None if unknown."""
    return _province_from(direccion, region, localidad)


def ccaa_for(*candidates: str | None) -> str:
    """Resolve a comunidad autónoma from a lead's location strings (region,
    localidad, address…). Uses the canonical province machinery first (incl. the
    address postal code), then a direct CCAA-name match, then UNKNOWN."""
    direccion = candidates[-1] if candidates else None
    region = candidates[0] if candidates else None
    localidad = candidates[1] if len(candidates) > 1 else None
    prov = _province_from(direccion, region, localidad)
    if prov:
        return _PROVINCE_CCAA.get(prov, UNKNOWN)
    # The string may already hold a CCAA name (BOE extract_region / data_repair).
    ccaa_self = {_norm(v): v for v in set(_PROVINCE_CCAA.values())}
    ccaa_self.update({"c. valenciana": "Comunidad Valenciana",
                      "comunidad valenciana": "Comunidad Valenciana",
                      "comunidad de madrid": "Madrid",
                      "principado de asturias": "Asturias"})
    for c in candidates:
        key = _norm(c)
        if key in ccaa_self:
            return ccaa_self[key]
    return UNKNOWN
