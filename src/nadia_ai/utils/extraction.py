import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
import os

from nadia_ai.config import OLLAMA_MODEL
from nadia_ai.scrapers.boe import extract_pdf_text

logger = logging.getLogger(__name__)

def clean_legal_text(text: str) -> str:
    """Strip common administrative noise (Notary/Court headers) from Spanish legal text."""
    if not text: return ""
    text = re.sub(r"(?i)do[ñn]a?\s+[A-Z\s]+,\s+Notario\s+del\s+Ilustre\s+Colegio\s+de\s+[A-Z\s]+,", "", text)
    text = re.sub(r"(?i)ante\s+m[ií],\s+[A-Z\s]+,\s+Notario\s+de\s+[A-Z\s]+,", "", text)
    text = re.sub(r"(?i)lo\s+que\s+se\s+hace\s+p[uú]blico\s+para\s+general\s+conocimiento.*", "", text)
    text = re.sub(r"(?i)hago\s+saber:?\s*", "", text)
    return text.strip()

EXTRACTION_PROMPT = """Eres un experto en derecho de sucesiones en España.
Analiza el texto y genera un objeto JSON con la siguiente estructura. Si un dato no aparece, usa null.

{{
  "deceased_name": "Nombre del fallecido/causante.",
  "date_of_death": "Fecha de fallecimiento (YYYY-MM-DD). CRÍTICO para calcular los días.",
  "list_of_heirs": ["Nombres COMPLETOS de los herederos o peticionarios."],
  "property_address": "Dirección del inmueble o finca. IGNORA la notaría.",
  "localidad": "Municipio/Ciudad",
  "provincia": "Provincia",
  "referencia_catastral": "Ref. Catastral si aparece"
}}

REGLAS CRÍTICAS:
1. PROHIBIDO: No uses la dirección de la Notaría (ej: 'Notaría de Telde') como 'property_address'.
2. Si el texto menciona 'acta de notoriedad' o 'declaración de herederos abintestato', busca el nombre del fallecido después de 'Don' o 'Doña'.
3. Extrae TODOS los herederos mencionados.
4. Responde ÚNICAMENTE con el objeto JSON.
5. OBLIGATORIO: Usa ÚNICAMENTE comillas dobles (") para claves y valores.
6. OBLIGATORIO: Empieza tu respuesta con '{{' y termínala con '}}'.

Texto a analizar:
{text}"""

_HEIR_PATTERNS = [
    re.compile(r"herederos?[:\s]+(?:(?:D\.?[aª]?\s+|D[ñÑ]a\.?\s+|[Dd]o[nñ]a?\s+)([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+(?:[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s*){1,3}))", re.I),
    re.compile(r"solicitada por (?:D\.?\s+|don\s+)([A-ZÁÉÍÓÚÑ][a-z\s]{5,40})", re.I),
]
_DECEASED_PATTERNS = [
    re.compile(r"fallecimiento de (?:D\.?\s+|don\s+)([A-ZÁÉÍÓÚÑ][a-z\s]{5,40})", re.I),
]

def extract_inheritance_data(text: str, causante_hint: str = None) -> dict:
    """Extract inheritance details using local Ollama."""
    try:
        import requests
        cleaned_text = clean_legal_text(text)
        truncated_text = cleaned_text[:10000]
        prompt = f"El causante/fallecido se llama: {causante_hint}\n\n" + EXTRACTION_PROMPT.format(text=truncated_text)
        
        response = requests.post(
            "http://localhost:11434/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0},
                "format": "json"
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
            # Generic/Fake patterns
            generics = ["calle principal", "calle 123", "calle falsa", "avenida principal", "calle ejemplo"]
            if any(g in addr_low for g in generics): return True
            # Very short addresses are suspicious
            if len(addr) < 8: return True
            # If it's just a number without a name
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
            "localidad": sanitize(result.get("localidad")),
            "provincia": sanitize(result.get("provincia")),
        }
    except Exception as e:
        logger.warning("Ollama extraction failed: %s", e)
        return {}

def run_heir_extraction(conn):
    """Enrich pending leads by extracting heirs and addresses from their source documents."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM leads
        WHERE (ai_extraction_done = 0 OR ai_extraction_done IS NULL)
          AND tier IN ('A', 'B', 'C')
        LIMIT 200
    """).fetchall()

    if not rows:
        logger.info("No leads pending heir extraction")
        return 0

    count = 0
    for row in rows:
        row_dict = dict(row)
        source_urls = json.loads(row_dict.get("source_urls", "[]"))
        if not source_urls:
            conn.execute("UPDATE leads SET ai_extraction_done = 1 WHERE id = ?", (row_dict["id"],))
            conn.commit()
            continue

        context = ""
        for url in source_urls:
            if "boe.es" in url:
                text = extract_pdf_text(url)
                if text: context += text + "\n"
        
        if not context:
            conn.execute("UPDATE leads SET ai_extraction_done = 1 WHERE id = ?", (row_dict["id"],))
            conn.commit()
            continue

        hint = row_dict.get("causante")
        result = extract_inheritance_data(context, hint)
        if not result:
            conn.execute("UPDATE leads SET ai_extraction_done = 1 WHERE id = ?", (row_dict["id"],))
            conn.commit()
            continue

        # Update lead
        updates = []
        params = []
        
        name = result.get("deceased_name")
        if name:
            updates.append("causante = COALESCE(NULLIF(causante, ''), ?)")
            params.append(name)

        dod = result.get("date_of_death")
        if dod:
            updates.append("date_of_death = COALESCE(NULLIF(date_of_death, ''), ?)")
            params.append(dod)
            # Recalculate days_since_death
            try:
                dt = datetime.strptime(dod, "%Y-%m-%d")
                days = (datetime.now() - dt).days
                updates.append("days_since_death = ?")
                params.append(days)
            except: pass

        heirs = result.get("list_of_heirs")
        if heirs:
            updates.append("heir_names_json = ?")
            params.append(json.dumps(heirs))
            updates.append("heir_name = ?")
            params.append(heirs[0])

        addr = result.get("property_address")
        if addr:
            updates.append("direccion = COALESCE(NULLIF(direccion, ''), ?)")
            params.append(addr)

        updates.append("ai_extraction_done = 1")
        updates.append("last_updated_at = datetime('now')")
        params.append(row_dict["id"])

        if updates:
            sql = f"UPDATE leads SET {', '.join(updates)} WHERE id = ?"
            conn.execute(sql, params)
            conn.commit()
            count += 1
            logger.info("Lead %d: extracted %d heirs, dod=%s", row_dict["id"], len(heirs or []), dod)

    return count
