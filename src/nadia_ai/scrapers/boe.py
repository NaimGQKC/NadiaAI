"""Scraper for BOE (Boletín Oficial del Estado) — inheritance publications.

Nationwide XML API Engine:
- Uses the BOE Open Data API to fetch daily summaries.
- Scans for inheritance-related documents in Sections IV (Judicial) and V (Administrative).
- Fetches full document text in XML format for reliable extraction.
"""

import hashlib
import logging
import os
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO

import requests
import pdfplumber
from bs4 import BeautifulSoup

from nadia_ai.models import EdictRecord

logger = logging.getLogger("nadia_ai.scrapers.boe")

# --- BOE Open Data API (XML) ---
BOE_SUMARIO_URL = "https://www.boe.es/datosabiertos/api/boe/sumario/{date}"
BOE_DOC_XML_URL = "https://www.boe.es/diario_boe/xml.php?id={doc_id}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/xml",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Name extraction patterns
# Using (?i:...) to make only the prefix case-insensitive.
# The actual name must start with an uppercase letter to avoid picking up "su padre" or "la causante".
NAME_PATTERNS = [
    re.compile(r"(?i:sucesi[oó]n\s+legal\s+de\s+(?:D\.?a?\s+|don\s+|do[ñn]a\s+|D[ñÑ]a\.?\s+)?)([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+(?:de\s+|del\s+|y\s+)?[A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+){1,5})(?i:\s+a\s+favor)?"),
    re.compile(r"(?i:herederos\s+(?:abintestato\s+)?de\s+(?:D\.?a?\s+|don\s+|do[ñn]a\s+|D[ñÑ]a\.?\s+)?)([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+(?:de\s+|del\s+|y\s+)?[A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+){1,5})"),
    re.compile(r"(?i:declaraci[oó]n\s+de\s+herederos.*?(?:D\.?a?\s+|don\s+|do[ñn]a\s+|D[ñÑ]a\.?\s+))([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+(?:de\s+|del\s+|y\s+)?[A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+){1,5})"),
    re.compile(r"(?i:(?:fallecimiento|defunci[oó]n)\s+de\s+(?:D\.?a?\s+|don\s+|do[ñn]a\s+|D[ñÑ]a\.?\s+)?)([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+(?:de\s+|del\s+|y\s+)?[A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+){1,5})"),
    re.compile(r"(?i:herencia\s+yacente\s+(?:de\s+)?(?:D\.?a?\s+|don\s+|do[ñn]a\s+|D[ñÑ]a\.?\s+)?)([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+(?:de\s+|del\s+|y\s+)?[A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+){1,5})"),
]

RC_PATTERN = re.compile(r"\b(\d{7}[A-Z]{2}\d{4}[A-Z]\d{4}[A-Z]{2})\b", re.IGNORECASE)

# Gemini 3.1 Pro Implementation: Deterministic Funnel
NEGATIVE_KEYWORDS = [
    "animal", "perro", "perra", "canino", "hallazgo", "objeto", "joya", "multa", 
    "tráfico", "vehículo", "sanción", "extranjería", "infracción", 
    "decomiso", "estupefacientes", "costas", "bicicleta", "anillo", "cartera",
    "documentación encontrada", "pérdida", "puntos", "radar"
]

def clean_summary_title(title: str) -> bool:
    """Check if title contains any negative keywords."""
    title_lower = title.lower()
    for word in NEGATIVE_KEYWORDS:
        if word in title_lower:
            return False
    return True

REGION_MAP = {
    # Andalucía
    "sevilla": "Andalucía", "málaga": "Andalucía", "malaga": "Andalucía", "córdoba": "Andalucía", "cordoba": "Andalucía", "granada": "Andalucía", "almería": "Andalucía", "almeria": "Andalucía", "jaén": "Andalucía", "jaen": "Andalucía", "huelva": "Andalucía", "cádiz": "Andalucía", "cadiz": "Andalucía",
    # Aragón
    "zaragoza": "Aragón", "huesca": "Aragón", "teruel": "Aragón",
    # Asturias
    "oviedo": "Asturias", "gijón": "Asturias", "gijon": "Asturias", "avilés": "Asturias",
    # Baleares
    "palma": "Baleares", "ibiza": "Baleares", "menorca": "Baleares", "mallorca": "Baleares",
    # Canarias
    "palmas": "Canarias", "tenerife": "Canarias", "lanzarote": "Canarias", "fuerteventura": "Canarias",
    # Cantabria
    "santander": "Cantabria", "torrelavega": "Cantabria",
    # Castilla-La Mancha
    "toledo": "Castilla-La Mancha", "albacete": "Castilla-La Mancha", "ciudad real": "Castilla-La Mancha", "cuenca": "Castilla-La Mancha", "guadalajara": "Castilla-La Mancha",
    # Castilla y León
    "valladolid": "Castilla y León", "burgos": "Castilla y León", "león": "Castilla y León", "leon": "Castilla y León", "salamanca": "Castilla y León", "segovia": "Castilla y León", "soria": "Castilla y León", "zamora": "Castilla y León", "palencia": "Castilla y León", "avila": "Castilla y León", "ávila": "Castilla y León",
    # Cataluña
    "barcelona": "Cataluña", "tarragona": "Cataluña", "lleida": "Cataluña", "girona": "Cataluña", "gerona": "Cataluña", "lérida": "Cataluña",
    # Extremadura
    "badajoz": "Extremadura", "cáceres": "Extremadura", "caceres": "Extremadura",
    # Galicia
    "coruña": "Galicia", "lugo": "Galicia", "ourense": "Galicia", "pontevedra": "Galicia", "vigo": "Galicia", "santiago": "Galicia",
    # Madrid
    "madrid": "Madrid", "getafe": "Madrid", "leganés": "Madrid", "alcorcón": "Madrid", "fuenlabrada": "Madrid", "móstoles": "Madrid", "alcalá": "Madrid",
    # Murcia
    "murcia": "Murcia", "cartagena": "Murcia",
    # Provinces
    "alava": "País Vasco", "albacete": "Castilla-La Mancha", "alicante": "C. Valenciana", "almeria": "Andalucía",
    "asturias": "Asturias", "avila": "Castilla y León", "badajoz": "Extremadura", "baleares": "Baleares",
    "barcelona": "Cataluña", "burgos": "Castilla y León", "caceres": "Extremadura", "cadiz": "Andalucía",
    "cantabria": "Cantabria", "castellon": "C. Valenciana", "ciudad real": "Castilla-La Mancha",
    "cordoba": "Andalucía", "coruña": "Galicia", "cuenca": "Castilla-La Mancha", "girona": "Cataluña",
    "granada": "Andalucía", "guadalajara": "Castilla-La Mancha", "gipuzkoa": "País Vasco",
    "huelva": "Andalucía", "huesca": "Aragón", "jaen": "Andalucía", "leon": "Castilla y León",
    "lleida": "Cataluña", "lugo": "Galicia", "madrid": "Madrid", "malaga": "Andalucía",
    "murcia": "Murcia", "navarra": "Navarra", "ourense": "Galicia", "palencia": "Castilla y León",
    "palmas": "Canarias", "pontevedra": "Galicia", "rioja": "La Rioja", "salamanca": "Castilla y León",
    "segovia": "Castilla y León", "sevilla": "Andalucía", "soria": "Castilla y León", "tarragona": "Cataluña",
    "tenerife": "Canarias", "teruel": "Aragón", "toledo": "Castilla-La Mancha", "valencia": "C. Valenciana",
    "valladolid": "Castilla y León", "bizkaia": "País Vasco", "zamora": "Castilla y León", "zaragoza": "Aragón",
    "ceuta": "Ceuta", "melilla": "Melilla",
    # Cities
    "vitoria": "País Vasco", "san sebastian": "País Vasco", "bilbao": "País Vasco", "oviedo": "Asturias",
    "gijon": "Asturias", "santander": "Cantabria", "logroño": "La Rioja", "pamplona": "Navarra",
}

def extract_region(text: str) -> str:
    if not text: return "Desconocida"
    text_lower = text.lower()
    # Normalize text (remove accents)
    from unicodedata import normalize
    text_lower = normalize('NFKD', text_lower).encode('ascii', 'ignore').decode('ascii')
    
    for key, region in REGION_MAP.items():
        if key in text_lower:
            return region
    return "Desconocida"

# Capitalized sentence-starters that follow a name in legal prose and get
# swallowed by the name patterns ("...de Josefa Serrano Canser Se hace saber")
_TRAILING_NOISE_TOKENS = {
    "se", "y", "de", "del", "la", "el", "que", "hace", "saber", "a", "ante", "en",
    # company-form suffixes swallowed from co-defendant names
    "sa", "sl", "slu", "sau", "scp", "cb",
}


def _strip_trailing_noise(name: str) -> str:
    words = name.split()
    while words and words[-1].lower() in _TRAILING_NOISE_TOKENS:
        words.pop()
    return " ".join(words)


def _extract_name(text: str) -> str | None:
    for pattern in NAME_PATTERNS:
        m = pattern.search(text)
        if m:
            name = _strip_trailing_noise(m.group(1).strip())
            if not name:
                continue
            if name.isupper(): name = name.title()
            return name
    return None

def fetch_boe_sumario(date: datetime) -> str:
    url = BOE_SUMARIO_URL.format(date=date.strftime("%Y%m%d"))
    try:
        response = SESSION.get(url, timeout=15)
        response.raise_for_status()
        return response.text
    except Exception as e:
        logger.debug("No summary for %s: %s", date.date(), e)
        return ""

def fetch_boe_doc_xml(doc_id: str) -> str:
    url = BOE_DOC_XML_URL.format(doc_id=doc_id)
    try:
        response = SESSION.get(url, timeout=15)
        response.raise_for_status()
        return response.text
    except Exception as e:
        logger.error("Failed to fetch %s: %s", doc_id, e)
        return ""

def extract_pdf_text(pdf_url: str) -> str:
    """Download a PDF and extract its text."""
    if not pdf_url:
        return ""
    try:
        logger.info("Extracting text from: %s", pdf_url)
        response = SESSION.get(pdf_url, timeout=20)
        response.raise_for_status()
        
        # BOE txt.php is an HTML page wrapping the edict in <div id="textoxslt">,
        # surrounded by ~15KB of site nav/chrome. Returning the raw HTML buries
        # the actual edict body (causante, DESTINATARIOS, último domicilio) under
        # menus and blows the downstream truncation budget. Extract just the body.
        ctype = response.headers.get("Content-Type", "").lower()
        if "txt.php" in pdf_url or ("html" in ctype) or ("text" in ctype and "pdf" not in ctype):
            soup = BeautifulSoup(response.text, "html.parser")
            body = soup.select_one("#textoxslt") or soup.select_one("div.documento")
            if body:
                return body.get_text(" ", strip=True)
            # Fallback: strip script/style/nav, then take page text.
            for tag in soup(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            return soup.get_text(" ", strip=True)
            
        text_parts = []
        with pdfplumber.open(BytesIO(response.content)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        
        full_text = "\n".join(text_parts)
        return full_text
    except Exception as e:
        logger.error("Failed to extract text from PDF %s: %s", pdf_url, e)
        return ""

def fetch_item_text(doc_id: str) -> str:
    """Fetch the XML content of a specific BOE document to check the body text."""
    url = f"https://www.boe.es/diario_boe/xml.php?id={doc_id}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            # Combine all paragraph text
            paragraphs = root.findall(".//texto/p")
            return " ".join([p.text for p in paragraphs if p.text])
    except Exception:
        pass
    return ""

def is_inheritance_text(text: str) -> bool:
    """Strict keyword check on the full document body."""
    keywords = [
        r"declaraci[oó]n\s+de\s+herederos",
        r"abintestato",
        r"sucesi[oó]n",
        r"herencia\s+yacente",
        r"causante",
        r"bienes\s+relictos",
        r"falleci[óo]n?",
        r"defunci[oó]n",
        r"herederos\s+de",
        r"testamentar[ií]a",
        r"esquela"
    ]
    return any(re.search(kw, text, re.IGNORECASE) for kw in keywords)

def extract_inheritance_doc_ids(sumario_xml: str) -> list[str]:
    """
    Parses the BOE Summary XML and identifies high-intent inheritance documents.
    Uses 'Deep Scan' for Judicial and Notary sections where titles are generic.
    """
    doc_ids = []
    try:
        root = ET.fromstring(sumario_xml.encode("utf-8"))
        
        # Primary keywords for summary-level filtering
        keywords = [
            r"herederos", r"sucesi[oó]n", r"abintestato", r"causante",
            r"falleci[óo]", r"defunci[oó]n", r"herencia", r"esquela"
        ]
        
        candidates_to_scan = []
        
        for seccion in root.findall(".//seccion"):
            sec_code = seccion.get("codigo") or ""
            # Section IV (Justicia) and V-C (Particulars/Notaries) are high-intent but generic titles
            is_high_intent_sec = sec_code == "4" or sec_code == "5C"
            # Section V-A/B are administrative noise
            is_admin_sec = sec_code.startswith("5A") or sec_code.startswith("5B")
            
            if not (is_high_intent_sec or is_admin_sec):
                continue
            
            for dept in seccion.findall(".//departamento"):
                dept_name = (dept.get("nombre") or "").lower()
                for item in dept.findall(".//item"):
                    item_id = item.findtext("identificador", "")
                    titulo = (item.findtext("titulo") or "").lower()
                    
                    if not clean_summary_title(titulo):
                        continue
                        
                    # FAST PATH: If the title is already explicit, grab it
                    text_to_check = f"{titulo} {dept_name}"
                    if any(re.search(kw, text_to_check, re.IGNORECASE) for kw in keywords):
                        # Ensure it's not the city of "Herencia"
                        if "herencia (ciudad real)" in titulo or "t.m. de herencia" in titulo:
                            continue
                        doc_ids.append(item_id)
                        continue
                    
                    # DEEP SCAN PATH: Determine if we should peek inside
                    should_deep_scan = False
                    
                    if is_high_intent_sec:
                        # Section IV (Justicia) and V-C (Particulars/Notaries) are ALWAYS deep scanned
                        should_deep_scan = True
                    elif is_admin_sec:
                        # Section V-A/B (Official/Contracting) - only scan if dept is related to State Assets or Notaries
                        scan_keywords = ["notar", "hacienda", "econom", "patrimonio", "tribut", "sucesi", "justicia", "recauda"]
                        if any(skw in dept_name for skw in scan_keywords):
                            should_deep_scan = True
                        elif any(skw in titulo for skw in scan_keywords):
                            should_deep_scan = True

                    if should_deep_scan:
                        candidates_to_scan.append(item_id)

        # Process candidates with Deep Scan (Concurrency for speed)
        if candidates_to_scan:
            logger.info("Deep scanning %d potential BOE documents for inheritance signals...", len(candidates_to_scan))
            # Use more workers for faster deep scan (BOE server handles it well)
            with ThreadPoolExecutor(max_workers=15) as executor:
                results = list(executor.map(lambda id: (id, fetch_item_text(id)), candidates_to_scan))
                
            for doc_id, text in results:
                if text and is_inheritance_text(text):
                    logger.info("Deep Scan MATCH found: %s", doc_id)
                    doc_ids.append(doc_id)
                    
    except Exception as e:
        logger.error("Error parsing sumario: %s", e)
    
    return list(set(doc_ids))

def parse_boe_document(xml_text: str, doc_id: str) -> EdictRecord | None:
    if not xml_text: return None
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
        title = root.findtext(".//titulo", "")
        pub_date_str = root.findtext(".//fecha_publicacion", "")
        pdf_url = root.findtext(".//url_pdf", "")
        if pdf_url and pdf_url.startswith("/"):
            pdf_url = f"https://www.boe.es{pdf_url}"
            
        pub_date = datetime.strptime(pub_date_str, "%Y%m%d").replace(tzinfo=UTC) if pub_date_str else None
        
        texto_el = root.find(".//texto")
        text = ET.tostring(texto_el, encoding="unicode", method="text") if texto_el is not None else ""
        combined = f"{title} {text}"
        
        causante = _extract_name(combined)
        region = extract_region(combined)
        rc_match = RC_PATTERN.search(combined)
        ref_catastral = rc_match.group(1).upper() if rc_match else None
        
        source_name = "boe_nationwide"
        if doc_id.startswith("BOE-B"):
            source_name = "boe_secv"
        elif doc_id.startswith("BOE-A"):
            # Section IV (Judicial) is also in Section A/B depending on sub-type
            # But let's keep Section V as the main priority
            pass

        return EdictRecord(
            source=source_name,
            source_id=doc_id,
            referencia_catastral=ref_catastral,
            edict_type="inheritance_lead",
            published_at=pub_date,
            source_url=f"https://www.boe.es/diario_boe/txt.php?id={doc_id}",
            causante=causante,
            localidad=region,
        )
    except Exception as e:
        logger.error("Error parsing doc %s: %s", doc_id, e)
        return None

# ── TEJU (Tablón Edictal Judicial Único, BOE-J) ────────────────────
# The daily TEJU index lists ~4,000 edicts/day with generic titles
# ("BARCELONA. Edicto ... procedimiento civil número 669/2024"), so
# title-level keyword filtering finds nothing. We use the BOE full-text
# search instead, which indexes the document body.

TEJU_SEARCH_URL = "https://www.boe.es/buscar/edictos_judiciales.php"
TEJU_QUERIES = ['"herencia yacente"', "herederos"]

# Names in TEJU edicts appear in ALL CAPS in the DESTINATARIOS block:
# "Herencia Yacente y Herederos Desconocidos de JOSE MANUEL PUENTE SANCLEMENTE"
TEJU_CAUSANTE_PATTERN = re.compile(
    r"(?i:herederos?\s+(?:desconocidos?\s+|ignorados?\s+|inciertos?\s+)?de\s+)"
    r"(?:D\.?ª?\s+|don\s+|do[ñn]a\s+)?"
    r"((?:[A-ZÁÉÍÓÚÑÜ]{2,}[\-']?\s+){1,5}[A-ZÁÉÍÓÚÑÜ]{2,})\b"
)
TEJU_LOCALIDAD_PATTERN = re.compile(
    r"Localidad:\s*(.+?)\s+(?:C[oó]digo\s+Postal|Provincia|Tel[eé]fono)"
)
TEJU_PROVINCIA_PATTERN = re.compile(
    r"Provincia:\s*(.+?)\s+(?:Tel[eé]fono|Correo|Fax|PROCEDIMIENTO)"
)


def _search_teju_ids(days: int) -> dict[str, datetime | None]:
    """Full-text search TEJU for inheritance edicts. Returns {doc_id: pub_date}."""
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    found: dict[str, datetime | None] = {}

    for query in TEJU_QUERIES:
        params = {
            "campo[0]": "DOC", "dato[0]": query, "operador[0]": "and",
            "campo[2]": "JUR", "dato[2]": "", "operador[2]": "and",
            "operador[4]": "and", "campo[4]": "FPU",
            "dato[4][0]": start.strftime("%Y-%m-%d"),
            "dato[4][1]": end.strftime("%Y-%m-%d"),
            "page_hits": "1000",
            "sort_field[0]": "FPU", "sort_order[0]": "desc",
            "accion": "Buscar",
        }
        try:
            r = SESSION.get(TEJU_SEARCH_URL, params=params, timeout=30)
            r.raise_for_status()
        except Exception as e:
            logger.error("TEJU search failed for %s: %s", query, e)
            continue

        soup = BeautifulSoup(r.text, "html.parser")
        hits = 0
        for li in soup.find_all("li"):
            a = li.find("a", href=re.compile(r"id=(BOE-J-\d+-\d+)"))
            if not a:
                continue
            doc_id = re.search(r"(BOE-J-\d+-\d+)", a.get("href", "")).group(1)
            if doc_id in found:
                continue
            pub_date = None
            date_m = re.search(r"de\s+(\d{2})/(\d{2})/(\d{4})", li.get_text(" ", strip=True))
            if date_m:
                d, mo, y = date_m.groups()
                try:
                    pub_date = datetime(int(y), int(mo), int(d), tzinfo=UTC)
                except ValueError:
                    pass
            found[doc_id] = pub_date
            hits += 1
        logger.info("TEJU search %s: %d new doc ids", query, hits)

    return found


def _fetch_teju_record(doc_id: str, pub_date: datetime | None) -> EdictRecord | None:
    """Fetch one TEJU edict text page and build a record."""
    from nadia_ai.utils.names import is_valid_person_name

    url = f"https://www.boe.es/diario_boe/txt.php?id={doc_id}"
    try:
        r = SESSION.get(url, timeout=20)
        r.raise_for_status()
    except Exception as e:
        logger.warning("TEJU doc fetch failed %s: %s", doc_id, e)
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    text = " ".join(soup.get_text(" ", strip=True).split())
    if not re.search(r"herencia\s+yacente|hereder", text, re.IGNORECASE):
        return None

    causante = None
    m = TEJU_CAUSANTE_PATTERN.search(text)
    if m:
        candidate = _strip_trailing_noise(m.group(1).strip()).title()
        if is_valid_person_name(candidate):
            causante = candidate
    if not causante:
        candidate = _extract_name(text)
        if candidate and is_valid_person_name(candidate):
            causante = candidate

    loc_m = TEJU_LOCALIDAD_PATTERN.search(text)
    prov_m = TEJU_PROVINCIA_PATTERN.search(text)
    localidad = loc_m.group(1).strip() if loc_m else None
    if not localidad and prov_m:
        localidad = prov_m.group(1).strip()

    rc_match = RC_PATTERN.search(text)

    return EdictRecord(
        source="boe_teju",
        source_id=doc_id,
        referencia_catastral=rc_match.group(1).upper() if rc_match else None,
        edict_type="inheritance_lead",
        published_at=pub_date,
        source_url=url,
        causante=causante,
        localidad=localidad,
        lugar_fallecimiento=prov_m.group(1).strip() if prov_m else None,
    )


def scrape_teju(days: int) -> list[EdictRecord]:
    """Scrape TEJU judicial inheritance edicts via BOE full-text search."""
    doc_ids = _search_teju_ids(days)
    if not doc_ids:
        return []
    logger.info("Fetching %d TEJU documents...", len(doc_ids))
    records = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(lambda kv: _fetch_teju_record(kv[0], kv[1]), doc_ids.items())
    for rec in results:
        if rec:
            records.append(rec)
    logger.info("TEJU: %d inheritance records", len(records))
    return records


# ── Notarial notifications (BOE-N) ─────────────────────────────────
# Notarial "declaración de herederos" announcements carry the causante + notary
# city in the index title: "NOTARÍA DE <NOTARY> DE <CITY>. Anuncio ... Acta de
# declaración de herederos Ab intestato de Doña <NAME>." That title alone makes a
# usable lead. The richer prize is in the per-doc not.php body, which the heir
# extraction step (utils/extraction.run_heir_extraction) fetches: it contains the
# FULL list of declared heirs (measured ~82% yield, frequently many heirs per
# declaration). This is the project's primary heir-name engine.

# Notarial heir-declarations are the highest-yield BOE signal (Sección V): the
# per-doc not.php page carries the FULL heir list (measured ~82% yield, often many
# heirs per declaration), and the index scan is cheap (one GET/day, no XML). So we
# sweep a much wider window than the main BOE pass. Estate sales run 3-18 months
# after the declaration, so a 90-day-old declaration is still a fresh listing lead —
# wider lookback = more sellable heir leads, not stale ones. Env-tunable per
# deployment via NADIA_NOTARIAL_DAYS (validated volumes: 10d→91, 30d→295).
NOTARIAL_LOOKBACK_DAYS = int(os.getenv("NADIA_NOTARIAL_DAYS", "90"))

NOTARIAL_KEYWORDS = re.compile(
    r"declaraci[oó]n\s+de\s+herederos|ab\s*intestato|herederos\s+de|sucesi[oó]n\s+intestada",
    re.IGNORECASE,
)
NOTARIAL_CAUSANTE_PATTERNS = [
    # "...de Doña María Isabel García Vidal." (name at end of title)
    re.compile(
        r"(?:de\s+)?(?:Don|Do[ñn]a|D\.?ª|Dª|D\.)\s+"
        r"([A-ZÁÉÍÓÚÑÜ][A-Za-zÁÉÍÓÚÑÜáéíóúñü'\-]+(?:\s+[A-Za-zÁÉÍÓÚÑÜáéíóúñü'\-]+){1,5})\s*\.?\s*$"
    ),
    # "...herederos abintestato de Don José Pérez Gil, ..." (name mid-title)
    re.compile(
        r"(?i:hereder\w+[^.]{0,40}?\bde\s+)(?:Don|Do[ñn]a|D\.?ª|Dª|D\.)?\s*"
        r"([A-ZÁÉÍÓÚÑÜ][A-Za-zÁÉÍÓÚÑÜáéíóúñü'\-]+(?:\s+[A-ZÁÉÍÓÚÑÜ][A-Za-zÁÉÍÓÚÑÜáéíóúñü'\-]+){1,5})"
    ),
]


def scrape_notarial_notifications(days: int) -> list[EdictRecord]:
    """Scrape BOE-N daily indexes for notarial heir-declaration announcements."""
    from nadia_ai.utils.names import is_valid_person_name

    records = []
    for i in range(days):
        date_obj = datetime.now(UTC) - timedelta(days=i)
        date_path = date_obj.strftime("%Y/%m/%d")
        url = f"https://www.boe.es/boe_n/dias/{date_path}/index.php?l=N"
        try:
            r = SESSION.get(url, timeout=30)
            if r.status_code != 200:
                continue
        except Exception as e:
            logger.warning("BOE-N index fetch failed for %s: %s", date_path, e)
            continue

        for item_html in re.findall(r'<li class="notif">(.*?)</li>', r.text, re.DOTALL):
            title_m = re.search(r"<p>(.*?)</p>", item_html, re.DOTALL)
            id_m = re.search(r"id=(BOE-N-\d+-\d+)", item_html)
            if not (title_m and id_m):
                continue
            title = " ".join(title_m.group(1).split())
            if not NOTARIAL_KEYWORDS.search(title):
                continue

            causante = None
            for pattern in NOTARIAL_CAUSANTE_PATTERNS:
                cm = pattern.search(title)
                if not cm:
                    continue
                candidate = cm.group(1).strip().strip(".,")
                if candidate.isupper():
                    candidate = candidate.title()
                if is_valid_person_name(candidate):
                    causante = candidate
                    break

            # City = last " DE " segment of the notary heading
            localidad = None
            head_m = re.match(r"NOTAR[ÍI]A\s+DE\s+(.+?)\.", title, re.IGNORECASE)
            if head_m and " DE " in head_m.group(1).upper():
                localidad = re.split(r"\s+DE\s+", head_m.group(1), flags=re.IGNORECASE)[-1]
                localidad = localidad.strip().title()

            records.append(EdictRecord(
                source="boe_n",
                source_id=id_m.group(1),
                edict_type="declaracion_herederos_abintestato",
                published_at=date_obj,
                source_url=f"https://www.boe.es/boe_n/dias/{date_path}/not.php?id={id_m.group(1)}",
                causante=causante,
                localidad=localidad,
                juzgado=head_m.group(0).rstrip(".").title() if head_m else None,
            ))
    logger.info("BOE-N notarial: %d records", len(records))
    return records

def scrape_boe(days: int = 90) -> list[EdictRecord]:
    """Scrape BOE Section IV, V, TEJU and Notificaciones for inheritance leads."""
    logger.info("Scraping BOE for the last %d days...", days)
    
    # 1. Scrape regular sections via XML API
    api_records = []
    seen_ids = set()
    for i in range(days):
        date = datetime.now(UTC) - timedelta(days=i)
        xml_data = fetch_boe_sumario(date)
        if not xml_data: continue
        
        doc_ids = extract_inheritance_doc_ids(xml_data)
        for doc_id in doc_ids:
            if doc_id in seen_ids: continue
            seen_ids.add(doc_id)
            
            # Fetch the full document XML to get rich data
            doc_xml = fetch_boe_doc_xml(doc_id)
            record = parse_boe_document(doc_xml, doc_id)
            if record:
                api_records.append(record)
            else:
                # Fallback if XML parsing fails
                api_records.append(EdictRecord(
                    source="boe_nationwide",
                    source_id=doc_id,
                    referencia_catastral=None,
                    edict_type="inheritance_lead",
                    published_at=date,
                    source_url=f"https://www.boe.es/diario_boe/txt.php?id={doc_id}",
                    address=None
                ))
            
    # 2. TEJU judicial edicts via full-text search + notarial announcements.
    # Notarial (Sección V "declaración de herederos") yields heirs ~65% vs ~3%
    # for judicial TEJU, and its scan is cheap (one index GET per day, no
    # per-doc XML). So give it a wider lookback than the expensive XML/TEJU
    # window — a 30-day sweep captures ~3x the notarial leads (validated:
    # 10d→91, 30d→295) that a 10-day daily window would otherwise miss.
    notarial_days = max(days, NOTARIAL_LOOKBACK_DAYS)
    supp_records = scrape_teju(days) + scrape_notarial_notifications(notarial_days)

    all_records = api_records + supp_records
    
    # Dedup by ID
    unique_records = []
    final_seen = set()
    for rec in all_records:
        if rec.source_id not in final_seen:
            final_seen.add(rec.source_id)
            unique_records.append(rec)
            
    logger.info("Total unique BOE leads found: %d", len(unique_records))
    return unique_records
