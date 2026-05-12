"""Scraper for BOE (Boletín Oficial del Estado) — inheritance publications.

Nationwide XML API Engine:
- Uses the BOE Open Data API to fetch daily summaries.
- Scans for inheritance-related documents in Sections IV (Judicial) and V (Administrative).
- Fetches full document text in XML format for reliable extraction.
"""

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
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

REGION_MAP = {
    "vigo": "Galicia", "santiago": "Galicia", "oviedo": "Asturias",
    "gijón": "Asturias", "gijon": "Asturias", "santander": "Cantabria",
    "logroño": "La Rioja", "logrono": "La Rioja",
    "toledo": "Castilla-La Mancha", "albacete": "Castilla-La Mancha",
    "ciudad real": "Castilla-La Mancha", "cáceres": "Extremadura",
    "caceres": "Extremadura", "badajoz": "Extremadura",
    "córdoba": "Andalucía", "cordoba": "Andalucía", "granada": "Andalucía",
    "almería": "Andalucía", "almeria": "Andalucía", "jaén": "Andalucía",
    "jaen": "Andalucía", "huelva": "Andalucía", "cádiz": "Andalucía",
    "cadiz": "Andalucía", "alicante": "C. Valenciana",
    "castellón": "C. Valenciana", "castellon": "C. Valenciana",
    "tarragona": "Cataluña", "girona": "Cataluña", "lleida": "Cataluña",
    "huesca": "Aragón", "teruel": "Aragón",
    "zaragoza": "Aragón", "madrid": "Madrid", "barcelona": "Cataluña",
    "vizcaya": "País Vasco", "bilbao": "País Vasco",
}

def extract_region(text: str) -> str:
    text_lower = text.lower()
    for city, region in REGION_MAP.items():
        if city in text_lower:
            return region
    return "Desconocida"

def _extract_name(text: str) -> str | None:
    for pattern in NAME_PATTERNS:
        m = pattern.search(text)
        if m:
            name = m.group(1).strip()
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
        
        # If it's a TXT page, return directly
        if "txt.php" in pdf_url or "text" in response.headers.get("Content-Type", "").lower():
            return response.text
            
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

def extract_inheritance_doc_ids(sumario_xml: str) -> list[str]:
    doc_ids = []
    if not sumario_xml: return doc_ids
    try:
        root = ET.fromstring(sumario_xml.encode("utf-8"))
        
        # New keywords to capture more quality leads
        keywords = [
            r"\bdeclaraci[oó]n\s+de\s+herederos\b",
            r"\bsucesi[oó]n\b",
            r"\babintestato\b",
            r"\bherederos\s+de\b",
            r"\bcausante\b",
            r"\bbienes\s+relictos\b",
            r"\bherencia\s+yacente\b",
            r"\bherencia\b",
            r"\bsucesi[oó]n\s+por\s+cesi[oó]n\b",
            r"\bt[ií]tulo\s+de\s+(?:marqu[eé]s|conde|duque|bar[oó]n|vizconde)\b",
            r"\bfallecimiento\b",
            r"\bdefunci[oó]n\b",
            r"\bedicto\b",
            r"\bnotificaci[oó]n\b",
            r"\bnotar[ií]a\b",
            r"\banuncio\s+de\s+subasta\b",
        ]
        
        # Iterate through the hierarchy correctly
        for dept in root.findall(".//departamento"):
            dept_name = (dept.get("nombre") or "").lower()
            for item in dept.findall(".//item"):
                item_id = item.findtext("identificador", "")
                if not item_id.startswith("BOE-B"): continue
                
                titulo = (item.findtext("titulo") or "").lower()
                
                # Check if title or department matches keywords
                text_to_check = f"{titulo} {dept_name}"
                
                if any(re.search(kw, text_to_check, re.IGNORECASE) for kw in keywords):
                    # Specific exclusion for the city of "Herencia"
                    if "herencia (ciudad real)" in titulo or "t.m. de herencia" in titulo:
                        logger.debug("Skipping city false positive: %s", titulo)
                        continue
                        
                    doc_ids.append(item_id)
                    
    except Exception as e:
        logger.error("Error parsing sumario: %s", e)
    return doc_ids

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
        
        return EdictRecord(
            source="boe_nationwide",
            source_id=doc_id,
            referencia_catastral=ref_catastral,
            edict_type="inheritance_lead",
            published_at=pub_date,
            source_url=pdf_url or f"https://www.boe.es/diario_boe/txt.php?id={doc_id}",
            causante=causante,
            localidad=region,
        )
    except Exception as e:
        logger.error("Error parsing doc %s: %s", doc_id, e)
        return None

def scrape_supplemental_leads(days: int) -> list[EdictRecord]:
    """Scrape TEJU (BOE-J) and Notificaciones (BOE-N) from web summaries."""
    all_records = []
    base_url = "https://www.boe.es"
    
    # Target supplements
    supplements = [
        ("boe_j", "J", "Edictos Judiciales"),
        ("boe_n", "N", "Notificaciones")
    ]
    
    keywords = [
        r"\bdeclaraci[oó]n\s+de\s+herederos\b",
        r"\bsucesi[oó]n\b",
        r"\babintestato\b",
        r"\bherederos\s+de\b",
        r"\bcausante\b",
        r"\bherencia\b",
        r"\bfallecimiento\b",
        r"\bdefunci[oó]n\b",
    ]
    
    for i in range(days):
        date_obj = datetime.now(UTC) - timedelta(days=i)
        date_path = date_obj.strftime("%Y/%m/%d")
        for folder, label, name in supplements:
            url = f"{base_url}/{folder}/dias/{date_path}/index.php?l={label}"
            try:
                r = SESSION.get(url, timeout=15)
                if r.status_code != 200: continue
                
                # Simple HTML parsing with regex
                items = re.findall(r'<li class="notif">(.*?)</li>', r.text, re.DOTALL)
                for item_html in items:
                    title_match = re.search(r'<p>(.*?)</p>', item_html, re.DOTALL)
                    if not title_match: continue
                    title = title_match.group(1).strip()
                    
                    id_match = re.search(r'id=(BOE-[JN]-\d+-\d+)', item_html)
                    if not id_match: continue
                    doc_id = id_match.group(1)
                    
                    if any(re.search(kw, title, re.IGNORECASE) for kw in keywords):
                        # Construct a record. We don't have all data yet, 
                        # but we can try to extract name from title.
                        causante = _extract_name(title)
                        region = extract_region(title)
                        
                        # Fetch full text for rich extraction immediately
                        doc_text = ""
                        try:
                            t_resp = requests.get(f"https://www.boe.es/diario_boe/txt.php?id={doc_id}", timeout=10)
                            if t_resp.status_code == 200:
                                doc_text = t_resp.text
                        except: pass

                        all_records.append(EdictRecord(
                            source=f"boe_{folder}",
                            source_id=doc_id,
                            edict_type="inheritance_lead",
                            published_at=date_obj,
                            source_url=f"https://www.boe.es/diario_boe/txt.php?id={doc_id}",
                            causante=causante,
                            localidad=region,
                            address=None,
                            full_text=doc_text
                        ))
            except Exception as e:
                logger.warning("Error scraping %s for %s: %s", name, date_path, e)
                
    return all_records

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
            
    # 2. Scrape supplements via web summaries
    supp_records = scrape_supplemental_leads(days)
    
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
