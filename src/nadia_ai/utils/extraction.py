import json
import logging
import re
import sqlite3
import os
from datetime import datetime
from pathlib import Path

from nadia_ai.config import OLLAMA_MODEL
from nadia_ai.scrapers.boe import extract_pdf_text

logger = logging.getLogger(__name__)

def clean_legal_text(text: str) -> str:
    """Strip common administrative noise (Notary/Court headers) from Spanish legal text.
    
    If the LLM never sees the Notary address, it can't hallucinate it as the property.
    """
    if not text: return ""
    
    # Mask Notary Addresses (e.g. "ante la Notaría... sita en Calle Mayor 1...")
    notary_pattern = r'(?i)(ante l[ao]s? Notar[ií]as?|Notario|Notar\S+ de).*?(sita en|ubicada en|calle|avda|plaza|P[ºª]?\s*)[^,.]+[,.]'
    text = re.sub(notary_pattern, '[DIRECCIÓN DE NOTARÍA OMITIDA].', text)
    
    # Strip footers like "Lo que se hace público para general conocimiento..."
    text = re.sub(r"(?i)lo\s+que\s+se\s+hace\s+p[uú]blico\s+para\s+general\s+conocimiento.*", "", text)
    
    # Strip formal greetings
    text = re.sub(r"(?i)hago\s+saber:?\s*", "", text)
    
    # Mask Generic Hallucination Triggers
    text = text.replace("Calle Principal 123", "")
    text = text.replace("Calle Principal", "")
    
    return text.strip()

EXTRACTION_PROMPT = """Eres un experto en derecho de sucesiones en España.
Analiza el texto y genera un objeto JSON con la estructura exacta de este ejemplo.
Si un dato no aparece o es genérico (ej: 'Calle Principal'), usa null.

{{
  "deceased_name": "Nombre del fallecido.",
  "date_of_death": "Fecha de fallecimiento (YYYY-MM-DD).",
  "list_of_heirs": ["Nombres completos."],
  "property_address": "Dirección exacta del inmueble. EXCLUYE notaría.",
  "referencia_catastral": "Ref. Catastral de 20 caracteres"
}}

REGLAS:
1. No inventes direcciones. Si no hay dirección de un inmueble claro, usa null.
2. Si mencionas 'Notaría de...', es incorrecto.
3. Responde SOLAMENTE el JSON.

Texto a analizar:
{text}"""

def extract_inheritance_data(text: str, causante_hint: str = None) -> dict:
    """Extract inheritance details using local Ollama REST API."""
    try:
        import requests
        cleaned_text = clean_legal_text(text)
        truncated_text = cleaned_text[:10000]
        
        prompt = f"El fallecido es: {causante_hint}\n\n" + EXTRACTION_PROMPT.format(text=truncated_text)
        
        response = requests.post(
            "http://localhost:11434/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {
                    "temperature": 0,
                    "num_ctx": 4096,
                },
                "format": "json",
                "keep_alive": "5m"
            },
            timeout=120
        )
        response.raise_for_status()
        resp_json = response.json()
        content = resp_json.get("message", {}).get("content", "")
        
        result = None
        if content:
            try: result = json.loads(content)
            except:
                m = re.search(r"\{.*\}", content, re.DOTALL)
                if m:
                    try: result = json.loads(m.group(0))
                    except: pass
        
        if not result: return {}

        def is_hallucinated_address(addr):
            if not addr: return True
            addr_low = str(addr).lower()
            generics = ["calle principal", "calle 123", "calle falsa", "avenida principal", "calle ejemplo"]
            if any(g in addr_low for g in generics): return True
            if len(addr) < 8: return True
            if re.match(r"^\d+$", addr): return True
            return False

        def sanitize(val):
            if val in (None, "null", "None", "unknown", "Desconocido"): return None
            if any(k in str(val).lower() for k in ("notar", "juzgado", "registro", "colegio")): return None
            return val

        heirs = result.get("list_of_heirs") or []
        if isinstance(heirs, str):
            heirs = [h.strip() for h in heirs.split(",") if h.strip()]
        heirs = [h for h in heirs if sanitize(h)]

        prop_addr = sanitize(result.get("property_address"))
        if is_hallucinated_address(prop_addr):
            prop_addr = None

        return {
            "deceased_name": sanitize(result.get("deceased_name")) or causante_hint,
            "date_of_death": sanitize(result.get("date_of_death")),
            "list_of_heirs": heirs,
            "property_address": prop_addr,
            "referencia_catastral": sanitize(result.get("referencia_catastral")),
        }
    except Exception as e:
        logger.warning("Ollama extraction failed: %s", e)
        return {}

def run_heir_extraction(conn):
    """Enrich pending leads by extracting heirs and addresses from their source documents.
    
    Includes Catastro validation as a gatekeeper for Tier A.
    """
    from nadia_ai.catastro import lookup_by_rc
    conn.row_factory = sqlite3.Row
    
    cursor = conn.execute("""
        SELECT id, causante, source_urls, tier 
        FROM leads 
        WHERE ai_extraction_done = 0 
        ORDER BY tier ASC, first_seen_at DESC 
        LIMIT 200
    """)
    leads = cursor.fetchall()
    
    count = 0
    for lead in leads:
        lead_id = lead["id"]
        causante = lead["causante"]
        urls = json.loads(lead["source_urls"] or "[]")
        
        # 1. Fetch full text (prioritize BOE if available)
        full_text = ""
        for url in urls:
            if "boe.es" in url:
                full_text = extract_pdf_text(url) # Using the utility from boe.py
                if full_text: break
        
        if not full_text:
            continue
            
        # 2. Extract entities
        data = extract_inheritance_data(full_text, causante_hint=causante)
        if not data: 
            continue
            
        # 3. Trust but Verify: Catastro Hook
        rc = data.get("referencia_catastral")
        if rc:
            logger.info("Validating RC %s for Lead %d via Catastro...", rc, lead_id)
            parcel = lookup_by_rc(rc)
            if not parcel:
                logger.warning("Lead %d: Invalid RC '%s'. Nullifying address.", lead_id, rc)
                data["referencia_catastral"] = None
                data["property_address"] = None
            elif parcel.address:
                data["property_address"] = parcel.address
        
        # 4. Determine final tier
        final_tier = "B"
        if data.get("property_address") or data.get("referencia_catastral"):
            final_tier = "A"
        
        # 5. Update Database
        heirs_json = json.dumps(data.get("list_of_heirs", []))
        primary_heir = data["list_of_heirs"][0] if data["list_of_heirs"] else None
        
        conn.execute("""
            UPDATE leads 
            SET heir_names_json = ?,
                heir_name = ?,
                direccion = ?,
                referencia_catastral = ?,
                date_of_death = ?,
                tier = ?,
                ai_extraction_done = 1,
                last_updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            heirs_json, 
            primary_heir, 
            data.get("property_address"), 
            data.get("referencia_catastral"),
            data.get("date_of_death"),
            final_tier,
            lead_id
        ))
        conn.commit()
        count += 1
        logger.info("Lead %d: Tier %s, heirs=%d", lead_id, final_tier, len(data.get("list_of_heirs", [])))
    
    return count
