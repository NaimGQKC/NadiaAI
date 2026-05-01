"""Scraper for BOA (Boletín Oficial de Aragón) — inheritance publications.

Uses the BOA Open Data JSON API (SEC=OPENDATABOAJSON) to find:
- Sucesión legal a favor de la Comunidad Autónoma de Aragón (intestate estates)
- Junta Distribuidora de Herencias (estate distribution announcements)
- Declaración de herederos publications

BOA publishes irregularly (not daily), so the pipeline is safe to run daily
even when BOA has no new content.
"""

import hashlib
import io
import logging
import re
import time
from datetime import UTC, datetime

import requests

from nadia_ai.models import EdictRecord

logger = logging.getLogger("nadia_ai.scrapers.boa")

BOA_BASE_URL = "https://www.boa.aragon.es"
BOA_API_URL = f"{BOA_BASE_URL}/cgi-bin/EBOA/BRSCGI"

HEADERS = {
    "User-Agent": "NadiaAI/0.1 (lead-generation research; contact: dev@example.com)",
    "Accept": "application/json",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Cadastral reference patterns
RC_PATTERN = re.compile(r"\b(\d{7}[A-Z]{2}\d{4}[A-Z]\d{4}[A-Z]{2})\b", re.IGNORECASE)
RC_PATTERN_SHORT = re.compile(r"(?:ref(?:erencia)?\.?\s*catastral)[:\s]+(\S+)", re.IGNORECASE)

# Name extraction from BOA titles
# "sucesion legal de D./D.a/Dña. FIRST LAST [a favor de|por la]"
NAME_FROM_TITLE = re.compile(
    r"sucesi[oó]n\s+legal\s+de\s+(?:D\.?[aª]?\s+|D[ñÑ]a\.?\s+|DO[NÑ]A?\s+)?"
    r"([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑa-záéíóúñ][a-záéíóúñA-ZÁÉÍÓÚÑ]+){1,5})"
    r"\s+(?:a\s+favor|por\s+la)",
    re.IGNORECASE,
)

# Name from body text: "causante", "fallecido/a", "difunto/a"
# Requires honorific (D./Dña./Don/Doña) to reduce false positives
NAME_FROM_TEXT = re.compile(
    r"(?:causante|fallecid[oa]|difunt[oa])[:\s]+"
    r"(?:D\.?[aª]?\s+|D[ñÑ]a\.?\s+|[Dd]o[nñ]a?\s+)"
    r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,4})",
    re.IGNORECASE,
)

# Fallback name pattern: "causante NAME" without honorific
# Lower priority — used only when NAME_FROM_TEXT doesn't match
NAME_FROM_TEXT_NO_HONORIFIC = re.compile(
    r"(?:causante|fallecid[oa]|difunt[oa])[:\s]+"
    r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,4})",
    re.IGNORECASE,
)

# "del causante" / "la causante" + optional honorific + NAME
NAME_DEL_CAUSANTE = re.compile(
    r"(?:del?|la)\s+causante\s+(?:D\.?[aª]?\s+|D[ñÑ]a\.?\s+|[Dd]o[nñ]a?\s+)?"
    r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,4})",
    re.IGNORECASE,
)

# "herencia de [honorific] NAME" (without "yacente")
NAME_HERENCIA_DE = re.compile(
    r"herencia\s+de\s+(?:D\.?[aª]?\s+|D[ñÑ]a\.?\s+|DO[NÑ]A?\s+|[Dd]o[nñ]a?\s+)?"
    r"(.+?)(?:\s*[,.;]|\s+a\s+favor|\s+por\s+)",
    re.IGNORECASE,
)

# "herencia yacente de" pattern for BOA titles
NAME_HERENCIA_YACENTE = re.compile(
    r"herencia\s+yacente\s+de\s+(?:D\.?[aª]?\s+|D[ñÑ]a\.?\s+|DO[NÑ]A?\s+|[Dd]o[nñ]a?\s+)?"
    r"([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑa-záéíóúñ][a-záéíóúñA-ZÁÉÍÓÚÑ]+){1,5})",
    re.IGNORECASE,
)

# "sucesión legal de NAME" — broader title pattern without "a favor" terminator
NAME_SUCESION_BROAD = re.compile(
    r"sucesi[oó]n\s+legal\s+de\s+(?:D\.?[aª]?\s+|D[ñÑ]a\.?\s+|DO[NÑ]A?\s+|[Dd]o[nñ]a?\s+)?"
    r"([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑa-záéíóúñ][a-záéíóúñA-ZÁÉÍÓÚÑ]+){1,5})"
    r"\s*[,.;$]?",
    re.IGNORECASE,
)

# Phrases that are clearly not person names (false positive blocklist)
_NOISE_PHRASES = {
    "la citada", "los hermanos", "hubiera tenido", "lugar de",
    "importe herencia", "en estado civil", "el día", "en fecha",
    "los que lo solicitan", "afirmaba tener", "en el hospital",
    "cuatro causantes", "tres causantes", "dos causantes",
    "cinco causantes", "seis causantes", "siete causantes",
    "ocho causantes", "nueve causantes", "diez causantes",
    "once causantes", "doce causantes", "trece causantes",
    "los causantes", "las causantes", "varios causantes", "veintitr",
    "de liquidaci", "cantidad propuesta", "tipo de",
    "la causante", "el causante", "la diputa", "procurador",
    "siendo parte", "ministerio", "inmueble de naturaleza",
    "inexistencia de parientes", "que lo solicitan",
    "que ya ostenta", "en ejulve", "en cuarte", "en alca",
    "en andorra", "en castels", "en zaragoza",
    "lugar importe", "alfredo mart",
}

# Spanish month names for date parsing
_SPANISH_MONTHS = {
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
    "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12",
}

# Death date: "fallecido/a el 5 de marzo de 2019" or "fallecido en Ejulve el 14 de marzo de 2008"
DEATH_DATE_PATTERN = re.compile(
    r"fallecid[oa]\s+(?:en\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ ,()]+?\s+)?"
    r"(?:el\s+)?(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})",
    re.IGNORECASE,
)

# Birth date: "nacido/a el 22 de febrero de 1926"
BIRTH_DATE_PATTERN = re.compile(
    r"nacid[oa]\s+(?:el\s+)?(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})",
    re.IGNORECASE,
)

# Birth place: "nacido/a ... en CITY" or "natural de CITY"
BIRTH_PLACE_PATTERN = re.compile(
    r"(?:nacid[oa].*?en\s+|natural\s+de\s+)"
    r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s*\([^)]+\))?)",
    re.IGNORECASE,
)

# Death place: "fallecido/a ... en CITY"
DEATH_PLACE_PATTERN = re.compile(
    r"fallecid[oa]\s+(?:el\s+\d{1,2}\s+de\s+\w+\s+de\s+\d{4}\s+)?en\s+"
    r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s*\([^)]+\))?)",
    re.IGNORECASE,
)

# Last domicile city: "último domicilio" or "vecino/a de CITY"
VECINO_PATTERN = re.compile(
    r"vecin[oa]\s+de\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s*\([^)]+\))?)",
    re.IGNORECASE,
)

# Address extraction from body text — Spanish street patterns
# Matches: "Calle ...", "CL ...", "Avda. ...", "Plaza ...", "Paseo ...", etc.
# Uses greedy match up to a sentence boundary (period, comma+space+lowercase, or semicolon)
ADDRESS_PATTERN = re.compile(
    r"(?:(?:C(?:alle)?|CL|Avda?\.?|Avenida|Plaza|Pza\.?|Paseo|P[ºª]?|"
    r"Camino|Ctra\.?|Carretera|Ronda|Travesía|Trav\.?|Glorieta)"
    r"\s+[A-ZÁÉÍÓÚÑ][^.;]{8,80}?"
    r"(?:,?\s*(?:n[ºª°.]?\s*)?\d{1,4}))"
    r"(?:\s*,?\s*(?:Zaragoza|Huesca|Teruel))?",
    re.IGNORECASE,
)

# Known government building addresses and false positive phrases
_GOV_ADDRESS_NOISE = {
    "plaza de los sitios",
    "plaza los sitios",
    "paseo maria agustin",
    "paseo maría agustín",
    "paseo de maria agustin",
    "paseo de maría agustín",
}
_ADDRESS_FALSE_POSITIVE_WORDS = {
    "mayor de edad", "soltera", "soltero", "fallecida", "fallecido",
    "mejora de la", "por este orden", "medio rural", "destino:",
    "climatización", "residencia", "vecina de", "vecino de",
}

# "domicilio en CITY" or "último domicilio en ADDRESS"
# Requires the address to contain a number (street number) to be useful
# Uses [^.;] to stop at sentence boundaries (period/semicolon)
DOMICILIO_PATTERN = re.compile(
    r"(?:[úu]ltimo\s+)?domicilio\s+en\s+"
    r"([A-ZÁÉÍÓÚÑ][^.;]{5,80}?\d+[^.;]{0,20}?)"
    r"(?:\.|,\s*(?:provincia|falleci|donde|de\s+(?:Zaragoza|Huesca|Teruel))|[);]|\s*$)",
    re.IGNORECASE,
)

# Broader domicilio pattern — captures "domicilio en CITY" without requiring a number
# Used as a locality fallback, not as a full address
DOMICILIO_LOCALITY_PATTERN = re.compile(
    r"(?:[úu]ltimo\s+)?domicilio\s+en\s+"
    r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+(?:de\s+(?:los\s+|las\s+|la\s+|el\s+)?)?[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)*)"
    r"(?:\s*\([^)]+\))?"
    r"(?:\s*[,.;)]|\s*$)",
    re.IGNORECASE,
)

# "sito/a en", "ubicado/a en", "radicante en" — property location patterns
# Common in Junta Distribuidora publications
SITO_EN_PATTERN = re.compile(
    r"(?:sit[oa]|ubicad[oa]|radicante)\s+en\s+"
    r"([A-ZÁÉÍÓÚÑ][^.;]{10,80}?\d+[^.;]{0,20}?)"
    r"(?:\.|,\s*(?:de\s+)?(?:Zaragoza|Huesca|Teruel|esta\s+(?:ciudad|localidad))|;|\s*$)",
    re.IGNORECASE,
)

# "Calle ... número ..." where "número" is spelled out instead of using n.º/nº
CALLE_NUMERO_PATTERN = re.compile(
    r"(?:(?:C(?:alle)?|CL|Avda?\.?|Avenida|Plaza|Pza\.?|Paseo|P[ºª]?|"
    r"Camino|Ctra\.?|Carretera|Ronda|Travesía|Trav\.?|Glorieta)"
    r"\s+[A-ZÁÉÍÓÚÑ][^.;]{5,60}?"
    r"(?:n[úu]mero|num\.?)\s*\d{1,4})"
    r"(?:\s*,?\s*(?:Zaragoza|Huesca|Teruel))?",
    re.IGNORECASE,
)

# "en la localidad de CITY, calle ..." compound pattern
LOCALIDAD_CALLE_PATTERN = re.compile(
    r"en\s+(?:la\s+)?(?:localidad|ciudad|población)\s+de\s+"
    r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+(?:de\s+(?:los\s+|las\s+|la\s+|el\s+)?)?[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)*)"
    r"(?:\s*\([^)]+\))?"
    r"(?:\s*,\s*"
    r"(?:(?:C(?:alle)?|CL|Avda?\.?|Avenida|Plaza|Pza\.?|Paseo)"
    r"\s+[A-ZÁÉÍÓÚÑ][^.;]{5,60}?\d{1,4}[^.;]{0,20}?))?"
    r"(?:\.|,|\s*$)",
    re.IGNORECASE,
)

# Locality: "en CITY (Zaragoza)" pattern
LOCALITY_PAREN_PATTERN = re.compile(
    r"en\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+(?:de\s+(?:los\s+|las\s+|la\s+|el\s+)?)?[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)*)"
    r"\s*\(\s*(?:Zaragoza|Huesca|Teruel)\s*\)",
    re.IGNORECASE,
)

# Locality: "residente en CITY"
RESIDENTE_PATTERN = re.compile(
    r"residente\s+en\s+"
    r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+(?:de\s+(?:los\s+|las\s+|la\s+|el\s+)?)?[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)*)"
    r"(?:\s*\([^)]+\))?",
    re.IGNORECASE,
)

# Juzgado/Notaría extraction patterns
# "Juzgado de Primera Instancia n.º 5 de Zaragoza"
JUZGADO_PATTERN = re.compile(
    r"(Juzgado\s+de\s+(?:Primera\s+Instancia|1[ªa]\s+Instancia)"
    r"(?:\s+e\s+Instrucci[oó]n)?"
    r"(?:\s+(?:n\.?[ºª°]?\s*|n[uú]mero\s+)\d{1,3})?"
    r"(?:\s+de\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+(?:de\s+(?:los\s+|las\s+|la\s+|el\s+)?)?[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)*)?)",
    re.IGNORECASE,
)

# "Notaría de D./Dña. NAME" or "Notario D./Dña. NAME"
NOTARIA_PATTERN = re.compile(
    r"((?:Notar[íi]a|Notario)\s+(?:de\s+)?(?:D\.?[aª]?\s+|D[ñÑ]a\.?\s+|[Dd]o[nñ]a?\s+)?"
    r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){0,3}"
    r"(?:\s*,?\s*(?:de\s+)?[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?)",
    re.IGNORECASE,
)

# Queries to run — each targets a different type of inheritance publication
SEARCH_QUERIES = [
    "sucesion+legal+comunidad+autonoma+aragon",
    "Junta+Distribuidora+Herencias",
    "declaracion+de+herederos",
]


def fetch_boa_json(
    text_query: str, since: datetime | None = None, max_docs: int = 100
) -> list[dict]:
    """Search BOA via the Open Data JSON API. Returns list of result dicts."""
    params = {
        "CMD": "VERLST",
        "OUTPUTMODE": "JSON",
        "BASE": "BOLE",
        "DOCS": f"1-{max_docs}",
        "SEC": "OPENDATABOAJSON",
        "SORT": "-PUBL",
        "SEPARADOR": "",
        "TITU-C": text_query,
    }
    if since:
        params["PUBL-GE"] = since.strftime("%Y%m%d")

    response = SESSION.get(BOA_API_URL, params=params, timeout=30)
    response.raise_for_status()

    try:
        data = response.json()
    except ValueError:
        logger.error("BOA returned non-JSON response for query: %s", text_query)
        return []

    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("result", data.get("results", [data]))
    return []


def _clean_boa_url(raw: str) -> str:
    """Clean BOA URL field — strip backticks/acute accents, extract first valid URL.

    BOA UrlPdf often looks like: "`https://...´`https://...´   "
    We want just the first clean https:// URL.
    """
    if not raw:
        return ""
    # Strip whitespace
    raw = raw.strip()
    # Remove backticks and acute accent characters
    raw = raw.replace("`", " ").replace("\u00b4", " ").replace("´", " ")
    # Split on whitespace and find the first thing that looks like a URL
    for part in raw.split():
        part = part.strip()
        if part.startswith("http"):
            return part
    return ""


def _parse_spanish_date(day: str, month_name: str, year: str) -> str | None:
    """Convert Spanish date parts to YYYY-MM-DD string."""
    mm = _SPANISH_MONTHS.get(month_name.lower())
    if not mm:
        return None
    return f"{year}-{mm}-{day.zfill(2)}"


def parse_boa_record(record: dict, query_label: str = "") -> EdictRecord | None:
    """Parse a single BOA JSON record into an EdictRecord."""
    title = record.get("Titulo", "") or ""
    text = record.get("Texto", "") or ""
    pub_date_str = record.get("FechaPublicacion", "") or ""
    doc_number = record.get("DOCN", "") or record.get("Numeroboletin", "") or ""
    pdf_url = record.get("UrlPdf", "") or ""

    if not title:
        return None

    # Build a stable source_id
    raw_id = f"boa:{doc_number}:{title[:80]}"
    source_id = hashlib.md5(raw_id.encode()).hexdigest()[:12]

    # Parse publication date (YYYYMMDD format)
    published_at = None
    if pub_date_str and len(pub_date_str) >= 8:
        try:
            published_at = datetime.strptime(pub_date_str[:8], "%Y%m%d").replace(tzinfo=UTC)
        except ValueError:
            pass

    # Extract causante name — try multiple patterns in priority order
    causante = None
    # 1. Strict title pattern: "sucesión legal de [honorific] NAME a favor de..."
    m = NAME_FROM_TITLE.search(title)
    if m:
        causante = m.group(1).strip()

    # 2. "herencia yacente de NAME" in title
    if not causante:
        m = NAME_HERENCIA_YACENTE.search(title)
        if m:
            causante = m.group(1).strip()

    # 3. Broader "sucesión legal de NAME" title pattern (no "a favor" required)
    if not causante:
        m = NAME_SUCESION_BROAD.search(title)
        if m:
            causante = m.group(1).strip()

    # 4. "herencia de NAME" in title or text
    if not causante:
        m = NAME_HERENCIA_DE.search(title) or (text and NAME_HERENCIA_DE.search(text))
        if m:
            causante = m.group(1).strip()

    # 5. Body text with honorific (most reliable body pattern)
    if not causante and text:
        m = NAME_FROM_TEXT.search(text)
        if m:
            causante = m.group(1).strip()

    # 6. "del causante NAME" / "la causante NAME" in body
    if not causante and text:
        m = NAME_DEL_CAUSANTE.search(text)
        if m:
            causante = m.group(1).strip()

    # 7. Fallback: body text without honorific (broader, lower priority)
    if not causante and text:
        m = NAME_FROM_TEXT_NO_HONORIFIC.search(text)
        if m:
            causante = m.group(1).strip()

    # Normalize ALL CAPS names
    if causante and causante.isupper():
        causante = causante.title()

    # Filter out false positive names (noise from body text parsing)
    if causante and (
        any(noise in causante.lower() for noise in _NOISE_PHRASES)
        or len(causante) <= 2
        or causante.lower().startswith("en ")
        or "\n" in causante
    ):
        causante = None

    # Extract referencia catastral from body text
    ref_catastral = None
    combined = f"{title} {text}"
    rc_match = RC_PATTERN.search(combined)
    if rc_match:
        ref_catastral = rc_match.group(1).upper()
    else:
        rc_match = RC_PATTERN_SHORT.search(combined)
        if rc_match:
            ref_catastral = rc_match.group(1).strip(" .,;:").upper()

    # Extract property address from body text
    address = None
    if text:
        # Try "domicilio en ..." first (most specific)
        dom_match = DOMICILIO_PATTERN.search(text)
        if dom_match:
            address = dom_match.group(1).strip().rstrip(",.")

        # Try "sito/a en" pattern (property location — common in Junta Distribuidora)
        if not address:
            sito_match = SITO_EN_PATTERN.search(text)
            if sito_match:
                address = sito_match.group(1).strip().rstrip(",.")

        # Try "Calle ... número ..." with spelled-out "número"
        if not address:
            cnum_match = CALLE_NUMERO_PATTERN.search(text)
            if cnum_match:
                address = cnum_match.group(0).strip().rstrip(",.")

        # Try standard street pattern (requires a street number)
        if not address:
            addr_match = ADDRESS_PATTERN.search(text)
            if addr_match:
                address = addr_match.group(0).strip().rstrip(",.")

        # Try "en la localidad de CITY, calle ..." compound pattern
        if not address:
            loc_calle_match = LOCALIDAD_CALLE_PATTERN.search(text)
            if loc_calle_match and loc_calle_match.group(0).strip():
                # Only use if it contains an actual street address (has a number)
                full_match = loc_calle_match.group(0).strip().rstrip(",.")
                if re.search(r"\d", full_match):
                    address = full_match

    # Filter out government building addresses, truncated junk, and false positives
    if address:
        addr_lower = address.lower().strip()
        if (
            len(address) < 12
            or any(gov in addr_lower for gov in _GOV_ADDRESS_NOISE)
            or any(fp in addr_lower for fp in _ADDRESS_FALSE_POSITIVE_WORDS)
        ):
            address = None

    # Extract extended fields from body text
    fecha_fallecimiento = None
    fecha_nacimiento = None
    lugar_nacimiento = None
    lugar_fallecimiento = None
    localidad = None

    if text:
        dm = DEATH_DATE_PATTERN.search(text)
        if dm:
            fecha_fallecimiento = _parse_spanish_date(dm.group(1), dm.group(2), dm.group(3))

        bm = BIRTH_DATE_PATTERN.search(text)
        if bm:
            fecha_nacimiento = _parse_spanish_date(bm.group(1), bm.group(2), bm.group(3))

        bp = BIRTH_PLACE_PATTERN.search(text)
        if bp:
            lugar_nacimiento = bp.group(1).strip()

        dp = DEATH_PLACE_PATTERN.search(text)
        if dp:
            lugar_fallecimiento = dp.group(1).strip()

        vm = VECINO_PATTERN.search(text)
        if vm:
            localidad = vm.group(1).strip()

        # Try "en CITY (Zaragoza)" pattern
        if not localidad:
            lp = LOCALITY_PAREN_PATTERN.search(text)
            if lp:
                localidad = lp.group(1).strip()

        # Try "residente en CITY"
        if not localidad:
            rm = RESIDENTE_PATTERN.search(text)
            if rm:
                localidad = rm.group(1).strip()

        # Try "con domicilio en CITY" (without street number — locality only)
        if not localidad:
            dom_loc = DOMICILIO_LOCALITY_PATTERN.search(text)
            if dom_loc:
                localidad = dom_loc.group(1).strip()

        # Try "en la localidad de CITY"
        if not localidad:
            loc_match = LOCALIDAD_CALLE_PATTERN.search(text)
            if loc_match:
                localidad = loc_match.group(1).strip()

        # If still no locality, infer from death place or domicilio
        if not localidad and lugar_fallecimiento:
            localidad = lugar_fallecimiento

    # Extract juzgado/notaría from title + body text
    juzgado = None
    juzgado_text = f"{title} {text}"
    jm = JUZGADO_PATTERN.search(juzgado_text)
    if jm:
        juzgado = jm.group(1).strip()
    if not juzgado:
        nm = NOTARIA_PATTERN.search(juzgado_text)
        if nm:
            juzgado = nm.group(1).strip()

    # Build source URL — BOA UrlPdf often contains backtick/acute-accent decoration
    # and two URLs concatenated: "`https://...´`https://...´"
    source_url = _clean_boa_url(pdf_url)
    if not source_url and doc_number:
        source_url = f"{BOA_BASE_URL}/cgi-bin/EBOA/BRSCGI?CMD=VEROBJ&MLKOB={doc_number}"

    return EdictRecord(
        source="boa",
        source_id=source_id,
        referencia_catastral=ref_catastral,
        edict_type="sucesion_legal_boa",
        published_at=published_at,
        source_url=source_url,
        causante=causante,
        address=address,
        fecha_fallecimiento=fecha_fallecimiento,
        fecha_nacimiento=fecha_nacimiento,
        lugar_nacimiento=lugar_nacimiento,
        lugar_fallecimiento=lugar_fallecimiento,
        localidad=localidad,
        juzgado=juzgado,
    )


_REPLACEMENT_MAP = {
    "\ufffd": "",  # drop replacement chars for pattern matching
}

# PDF-specific name patterns — tolerant of encoding garble (use \S and . instead of accented chars)
# "sucesión legal de D.ª NAME a favor"
_PDF_NAME_SUCESION = re.compile(
    r"sucesi\S*n\s+legal\s+de\s+\S{1,5}\s+"
    r"([A-Z][a-z\S]+(?:\s+[A-Z][a-z\S]+){1,5})"
    r"\s+a\s+favor",
    re.IGNORECASE,
)

# "herencia yacente de [honorific] NAME" in PDF
_PDF_NAME_HERENCIA_YACENTE = re.compile(
    r"herencia\s+yacente\s+de\s+(?:\S{1,5}\s+)?"
    r"([A-Z][a-z\S]+(?:\s+[A-Z][a-z\S]+){1,5})",
    re.IGNORECASE,
)

# "causante [honorific] NAME" in PDF
_PDF_NAME_CAUSANTE = re.compile(
    r"causante\s+(?:\S{1,5}\s+)?"
    r"([A-Z][a-z\S]+(?:\s+[A-Z][a-z\S]+){1,4})",
    re.IGNORECASE,
)

# Junta Distribuidora table format: "LASTNAME LASTNAME, FIRSTNAME ... PARCIAL|GENERAL"
_PDF_NAME_TABLE = re.compile(
    r"\n\s*([A-Z][A-Z\s]+),\s+([A-Z][A-Z\s]+?)\s+(?:PARCIAL|GENERAL)\s",
)

# "herencia de [honorific] NAME" in PDF
_PDF_NAME_HERENCIA_DE = re.compile(
    r"herencia\s+(?:vacante\s+)?de\s+(?:\S{1,5}\s+)?"
    r"([A-Z][a-z\S]+(?:\s+[A-Z][a-z\S]+){1,5})"
    r"(?:\s*[,.;]|\s+a\s+favor|\s+por\s+)",
    re.IGNORECASE,
)


def _clean_pdf_text(raw: str) -> str:
    """Clean PDF-extracted text: fix encoding artifacts, normalize whitespace."""
    for old, new in _REPLACEMENT_MAP.items():
        raw = raw.replace(old, new)
    # Collapse multiple spaces
    raw = re.sub(r" {2,}", " ", raw)
    return raw


def _fetch_pdf_bytes(url: str) -> bytes | None:
    """Fetch a BOA PDF. Returns raw bytes or None on failure."""
    try:
        resp = SESSION.get(url, timeout=20)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        logger.debug("PDF fetch failed for %s: %s", url, e)
        return None


def _fetch_pdf_text(url: str) -> str:
    """Fetch a BOA PDF and extract its text content. Returns empty string on failure."""
    pdf_bytes = _fetch_pdf_bytes(url)
    if not pdf_bytes:
        return ""

    # Try pdfplumber first (better table extraction), fall back to pypdf
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text_parts = []
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
            return _clean_pdf_text("\n".join(text_parts))
    except ImportError:
        pass
    except Exception as e:
        logger.debug("pdfplumber failed for %s: %s, trying pypdf", url, e)

    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text_parts = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
        return _clean_pdf_text(" ".join(text_parts))
    except ImportError:
        logger.warning("Neither pdfplumber nor pypdf installed — skipping PDF")
        return ""
    except Exception as e:
        logger.debug("pypdf failed for %s: %s", url, e)
        return ""


def _enrich_from_pdf(record: EdictRecord) -> EdictRecord:
    """For records missing a causante name, fetch the PDF and re-extract fields."""
    if record.causante or not record.source_url:
        return record

    text = _fetch_pdf_text(record.source_url)
    if not text:
        return record

    updates: dict = {}

    # Try Junta Distribuidora table format first: "LASTNAME, FIRSTNAME PARCIAL|GENERAL"
    table_match = _PDF_NAME_TABLE.search(text)
    if table_match:
        apellidos = table_match.group(1).strip().title()
        nombre = table_match.group(2).strip().title()
        updates["causante"] = f"{nombre} {apellidos}"
        logger.info("PDF table enriched: %s (id=%s)", updates["causante"], record.source_id)

    # Try PDF-tolerant patterns first (handle encoding garble), then originals
    if "causante" not in updates:
        for pattern in [
            _PDF_NAME_SUCESION, _PDF_NAME_HERENCIA_YACENTE, _PDF_NAME_HERENCIA_DE,
            _PDF_NAME_CAUSANTE,
            NAME_FROM_TITLE, NAME_HERENCIA_YACENTE, NAME_SUCESION_BROAD,
            NAME_HERENCIA_DE, NAME_FROM_TEXT, NAME_DEL_CAUSANTE,
            NAME_FROM_TEXT_NO_HONORIFIC,
        ]:
            m = pattern.search(text)
            if m:
                name = m.group(1).strip()
                if name.isupper():
                    name = name.title()
                if not (
                    any(noise in name.lower() for noise in _NOISE_PHRASES)
                    or len(name) <= 2
                    or name.lower().startswith("en ")
                    or "\n" in name
                ):
                    updates["causante"] = name
                    logger.info("PDF enriched: %s (id=%s)", name, record.source_id)
                break

    # Also try to fill in missing address from PDF text
    if not record.address:
        for pat in [DOMICILIO_PATTERN, SITO_EN_PATTERN, CALLE_NUMERO_PATTERN, ADDRESS_PATTERN]:
            m = pat.search(text)
            if m:
                addr = (m.group(1) if pat.groups else m.group(0)).strip().rstrip(",.")
                addr_lower = addr.lower()
                if (
                    len(addr) >= 12
                    and not any(gov in addr_lower for gov in _GOV_ADDRESS_NOISE)
                    and not any(fp in addr_lower for fp in _ADDRESS_FALSE_POSITIVE_WORDS)
                ):
                    updates["address"] = addr
                    break

    # Also try to fill in missing locality from PDF text
    if not record.localidad:
        for pat in [VECINO_PATTERN, LOCALITY_PAREN_PATTERN, RESIDENTE_PATTERN, DOMICILIO_LOCALITY_PATTERN]:
            m = pat.search(text)
            if m:
                updates["localidad"] = m.group(1).strip()
                break

    # Also try to fill in missing juzgado from PDF text
    if not record.juzgado:
        jm = JUZGADO_PATTERN.search(text)
        if jm:
            updates["juzgado"] = jm.group(1).strip()
        else:
            nm = NOTARIA_PATTERN.search(text)
            if nm:
                updates["juzgado"] = nm.group(1).strip()

    if updates:
        return record.model_copy(update=updates)
    return record


def scrape_boa(since: datetime | None = None) -> list[EdictRecord]:
    """Scrape BOA for inheritance-related publications.

    Runs multiple search queries and deduplicates by source_id.
    Safe to run daily — returns empty list when BOA has no new content.
    """
    seen_ids = set()
    all_records = []

    for query in SEARCH_QUERIES:
        try:
            raw_results = fetch_boa_json(query, since=since)
            logger.info("BOA query '%s': %d results", query, len(raw_results))

            for raw in raw_results:
                record = parse_boa_record(raw, query_label=query)
                if record and record.source_id not in seen_ids:
                    seen_ids.add(record.source_id)
                    all_records.append(record)
                    if record.causante:
                        logger.info("BOA lead: %s (id=%s)", record.causante, record.source_id)

        except requests.RequestException as e:
            logger.error("BOA query '%s' failed: %s", query, e)

    # Second pass: fetch PDFs for records missing a causante name
    nameless = [r for r in all_records if not r.causante and r.source_url]
    if nameless:
        logger.info("BOA: %d records missing causante — fetching PDFs", len(nameless))
        enriched_count = 0
        for i, record in enumerate(nameless):
            idx = all_records.index(record)
            all_records[idx] = _enrich_from_pdf(record)
            if all_records[idx].causante:
                enriched_count += 1
            # Polite delay: 0.5s between PDF fetches to not hammer BOA
            if i < len(nameless) - 1:
                time.sleep(0.5)
        logger.info("BOA PDF enrichment: recovered %d/%d names", enriched_count, len(nameless))

    logger.info("BOA scrape complete: %d unique records", len(all_records))
    return all_records
