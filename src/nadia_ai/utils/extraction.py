import json
import logging
import re
import sqlite3
import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from pathlib import Path

from nadia_ai.scrapers.boe import extract_pdf_text
from nadia_ai.utils.names import clean_name_list, is_valid_person_name

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

def is_worth_llm_compute(full_text: str) -> bool:
    """Acts as a cheap gatekeeper. 
    If the text doesn't contain at least one strong inheritance signal, 
    do not waste local Mistral tokens on it.
    """
    if not full_text: return False
    
    # High-signal inheritance keywords (Gemini 3.1 Pro recommendation)
    inheritance_pattern = r'(fallecid[oa]|falleci[óo]|causante|hereder[oa]s?|abintestato|testamentar|sucesi[óo]n|legatario|albacea|defunci[óo]n|esquela)'
    
    # If we don't find these words, it's not an inheritance edict.
    if not re.search(inheritance_pattern, full_text, re.IGNORECASE):
        return False
        
    return True

EXTRACTION_PROMPT = """Eres un experto en derecho de sucesiones en España.
Analiza el texto (que puede ser un edicto judicial o una esquela/obituario) y genera un objeto JSON.
Si un dato no aparece, usa null.

{{
  "deceased_name": "Nombre completo del fallecido.",
  "date_of_death": "Fecha de fallecimiento (YYYY-MM-DD).",
  "list_of_heirs": ["Nombres completos de hijos, viudo/a, o herederos mencionados. Ignorar nombres de sobrinos o primos si hay hijos."],
  "property_address": "Dirección de residencia del fallecido (calle, número, piso, ciudad). Si el texto dice 'vecino de [Ciudad]', pon la ciudad. No uses direcciones de Tanatorios.",
  "referencia_catastral": "Ref. Catastral si aparece (20 caracteres)."
}}

REGLAS CRÍTICAS:
1. Si el texto es una esquela, busca los nombres de los hijos (ej: 'Sus hijos: Juan y María').
2. Diferencia entre el lugar del fallecimiento (hospital/tanatorio) y el domicilio o vecindad (ej: 'vecino de Zaragoza').
3. Responde SOLAMENTE el JSON.

Texto a analizar:
{text}"""

def extract_heirs_regex(text: str) -> list[str]:
    """Fallback regex to find potential heir names when LLM fails."""
    heirs = []
    # Pattern for "interesados: [NAME], [NAME]..."
    interesados_match = re.search(r'(?i)interesados:?\s*([A-ZÁÉÍÓÚÑ\s,]+)', text)
    if interesados_match:
        names = interesados_match.group(1).split(',')
        for n in names:
            n = n.strip()
            if len(n.split()) >= 2: # At least two words (First Last)
                heirs.append(n.title())

    # Pattern for "herederos de D. [NAME]"
    herederos_match = re.findall(r'(?i)herederos\s+de\s+(?:D\.|Doña|Don)?\s*([A-ZÁÉÍÓÚÑ\s]+?)(?=\s+y\s+|\s+o\s+|\s+en\s+|$|[.,])', text)
    for h in herederos_match:
        h = h.strip()
        if len(h.split()) >= 2:
            heirs.append(h.title())

    # Both patterns happily capture title-cased legal boilerplate
    # ("Si En El Plazo De Un Mes...") — keep only plausible person names.
    return clean_name_list(heirs)

def _extract_via_llm(prompt: str) -> dict | None:
    """Extract via the configured OpenAI-compatible extraction model (DeepSeek by
    default; swap to Qwen/MiniMax/etc. via the EXTRACTION_* env vars). Works in CI.
    Returns the raw 5-key dict, or None to defer to regex."""
    from nadia_ai.config import EXTRACTION_API_KEY, EXTRACTION_API_URL, EXTRACTION_MODEL
    if not EXTRACTION_API_KEY:
        return None
    try:
        resp = requests.post(
            EXTRACTION_API_URL,
            headers={
                "Authorization": f"Bearer {EXTRACTION_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": EXTRACTION_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                # Generous cap so reasoning models (e.g. MiniMax M3) have room to
                # think AND still emit the JSON — too small truncates to empty.
                "max_tokens": 2000,
                "response_format": {"type": "json_object"},
            },
            timeout=90,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        if not content:
            return None
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", content, re.DOTALL)
            return json.loads(m.group(0)) if m else None
    except Exception as e:
        logger.warning("LLM extraction failed, falling back to regex: %s", e)
        return None


def extract_inheritance_data(text: str, causante_hint: str = None) -> dict:
    """Extract inheritance details with the configured LLM (if EXTRACTION_API_KEY
    is set), else regex. The downstream sanitization is identical for both."""
    try:
        cleaned_text = clean_legal_text(text)
        truncated_text = cleaned_text[:10000]

        prompt = f"El fallecido es: {causante_hint}\n\n" + EXTRACTION_PROMPT.format(text=truncated_text)

        # Configured cloud extraction model if a key is set, else regex.
        result = _extract_via_llm(prompt)
        if result is None:
            logger.info("No LLM available — using regex extraction.")
            result = {
                "deceased_name": causante_hint,
                "list_of_heirs": extract_heirs_regex(text),
                "property_address": None,
                "referencia_catastral": None,
            }

        if not result: return {}

        def is_hallucinated_address(addr):
            if not addr: return True
            addr_low = str(addr).lower()
            generics = ["calle principal", "calle 123", "calle falsa", "avenida principal", "calle ejemplo"]
            if any(g in addr_low for g in generics): return True
            if len(addr) < 8: return True
            if re.match(r"^\d+$", addr): return True
            return False

        def is_funeral_address(addr):
            if not addr: return False
            from nadia_ai.merge import strip_accents
            addr_low = strip_accents(str(addr)).lower()
            
            funeral_keywords = [
                "tanatorio", "cementerio", "crematorio", "velatorio", "funeraria", 
                "parcemasa", "camposanto", "tumba", "nicho", "sepultura", 
                "morgue", "tanatori", "cementiri", "crematori", "servisa", 
                "memora", "capilla ardiente", "funeral", "tanatoris", "cementerios"
            ]
            if any(k in addr_low for k in funeral_keywords):
                return True
                
            known_tanatorios = [
                "camino viejo de picassent",
                "colonia de sta. ines",
                "colonia de santa ines",
                "carretera vella de valencia",
                "avenida de castrelos",
                "camino de las torres, 73",
                "camino de las torres 73",
                "placa pau picasso",
                "fray julian garces",
                "fray julian garces",
                "huerta de la fontanilla",
                "miguel romero martinez",
                "loma del esparragal",
                "calle del desconsuelo",
                "carretera n-iv",
                "calle del cementerio",
                "camino del cementerio",
                "junto al cementerio",
                "junto cementerio",
                "calle caleiro, 81",
                "calle caleiro 81",
                "calle caleiro",
                "avenida da ponte, 106",
                "avenida da ponte 106",
                "avenida da ponte",
                "calle canales, 7",
                "calle canales 7",
                "carretera de can ruti",
                "carretera de montcada, 789",
                "carretera de montcada 789",
                "paseo de la sabica",
                "rambla santa ana, 42",
                "rambla santa ana 42",
                "calle aguila",
                "calle aguila",
                "camino de acedinos",
                "camino de san antonio,43",
                "camino de san antonio 43",
                "camino de san antonio, 43",
                "camino de san antonio",
                "camino de los leneros",
                "camino de los leneros",
                "pau redo",
                "los arenales",
                "joan cid i mulet",
                "calle restauracion, 2",
                "calle restauracion 2",
                "pol. ind. matallana"
            ]
            if any(kt in addr_low for kt in known_tanatorios):
                return True
                
            return False

        def sanitize(val):
            if val in (None, "null", "None", "unknown", "Desconocido"): return None
            if any(k in str(val).lower() for k in ("notar", "juzgado", "registro", "colegio")): return None
            return val

        heirs = result.get("list_of_heirs") or []
        if isinstance(heirs, str):
            heirs = [h.strip() for h in heirs.split(",") if h.strip()]
        heirs = [h for h in heirs if sanitize(h)]
        # Drop LLM placeholder echoes ("Nombres completos no proporcionados
        # en el texto") and any other non-name strings.
        heirs = clean_name_list(heirs)

        # If LLM didn't find heirs but regex does, merge them
        if not heirs:
            heirs = extract_heirs_regex(text)

        prop_addr = sanitize(result.get("property_address"))
        if is_hallucinated_address(prop_addr) or is_funeral_address(prop_addr):
            prop_addr = None

        deceased = sanitize(result.get("deceased_name"))
        if deceased and not is_valid_person_name(str(deceased)):
            deceased = None

        return {
            "deceased_name": deceased or causante_hint,
            "date_of_death": sanitize(result.get("date_of_death")),
            "list_of_heirs": heirs,
            "property_address": prop_addr,
            "referencia_catastral": sanitize(result.get("referencia_catastral")),
        }
    except Exception as e:
        logger.warning("Extraction failed entirely: %s", e)
        return {}

def run_heir_extraction(conn):
    """Enrich pending leads by extracting heirs and addresses from their source documents.
    
    Includes Catastro validation as a gatekeeper for Tier A.
    """
    from nadia_ai.catastro import lookup_by_rc
    conn.row_factory = sqlite3.Row
    
    cursor = conn.execute("""
        SELECT id, causante, source_urls, tier, sources 
        FROM leads 
        WHERE ai_extraction_done = 0 
        ORDER BY 
            CASE 
                WHEN sources LIKE '%BOE%' OR sources LIKE '%Tablón%' OR sources LIKE '%BOA%' OR sources LIKE '%BOP%' THEN 0 
                ELSE 1 
            END ASC,
            tier ASC, 
            first_seen_at DESC 
        LIMIT 200
    """)
    leads = cursor.fetchall()
    
    count = 0
    for lead in leads:
        lead_id = lead["id"]
        causante = lead["causante"]
        urls = json.loads(lead["source_urls"] or "[]")
        
        # 1. Fetch full text
        full_text = ""
        for url in urls:
            fetch_url = url
            if "zaragoza.es/sede/servicio/tablon-edicto/" in url and not url.endswith("/document"):
                fetch_url = url + "/document"
                
            if "boe.es" in fetch_url:
                full_text = extract_pdf_text(fetch_url)
            else:
                # Generic webpage text extraction
                try:
                    r = requests.get(fetch_url, timeout=15)
                    if r.status_code == 200:
                        soup = BeautifulSoup(r.text, "html.parser")
                        # Try to find main content or just get all text
                        main = soup.select_one(".content, #main, article, .esquela-detalle, .texto-esquela")
                        full_text = main.get_text(separator=" ", strip=True) if main else soup.get_text(separator=" ", strip=True)
                except Exception as e:
                    logger.warning("Failed to fetch text from %s: %s", fetch_url, e)
            
            if full_text: break
        
        if not full_text:
            # No fetchable text (dead link, PDF-only source, parse failure).
            # Mark done so the lead stops re-entering the 200-per-run queue
            # and clogging it for leads whose documents ARE readable.
            logger.info("Lead %d: no fetchable text — marking extraction done.", lead_id)
            conn.execute("UPDATE leads SET ai_extraction_done = 1 WHERE id = ?", (lead_id,))
            conn.commit()
            continue
            
        # 2. Deterministic Funnel: The "Wake Word" check
        if not is_worth_llm_compute(full_text):
            logger.info("Skipping Lead %d: Failed deterministic check (no inheritance keywords).", lead_id)
            conn.execute("UPDATE leads SET ai_extraction_done = 1, tier = 'C' WHERE id = ?", (lead_id,))
            conn.commit()
            continue
            
        # Check if the lead is obituary-only
        sources_list = json.loads(lead["sources"] or "[]")
        obituary_only = all(s in ("Esquelas", "Defunciones", "iEsquelas", "rememori") for s in sources_list)

        # 3. Extract entities
        data = extract_inheritance_data(full_text, causante_hint=causante)
        if not data: 
            continue
            
        if obituary_only:
            # Obituaries never list residential property addresses
            data["property_address"] = None
            data["referencia_catastral"] = None
            
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
        heirs_list = data.get("list_of_heirs", [])
        if not isinstance(heirs_list, list):
            heirs_list = [str(heirs_list)]
        heirs_list = clean_name_list(heirs_list)
        heirs_json = json.dumps(heirs_list)
        primary_heir = heirs_list[0] if heirs_list else None
        
        death_date = data.get("date_of_death")
        if isinstance(death_date, list):
            death_date = death_date[0] if death_date else None
        death_date = str(death_date) if death_date else None

        prop_addr = data.get("property_address")
        rc_val = data.get("referencia_catastral")
        # address_norm / ref_catastral are the match keys used by merge dedup and
        # enrichment cross-joins — keep them in sync with the display columns so
        # a resolved RC/address actually participates in obras/subastas matching.
        from nadia_ai.merge import normalize_address
        addr_norm = normalize_address(prop_addr) if prop_addr else None

        conn.execute("""
            UPDATE leads
            SET heir_names_json = ?,
                heir_name = ?,
                direccion = ?,
                address_norm = COALESCE(?, address_norm),
                referencia_catastral = ?,
                ref_catastral = COALESCE(NULLIF(?, ''), ref_catastral),
                date_of_death = ?,
                tier = ?,
                ai_extraction_done = 1,
                last_updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            heirs_json,
            primary_heir,
            prop_addr,
            addr_norm,
            rc_val,
            rc_val or "",
            death_date,
            final_tier,
            lead_id
        ))
        conn.commit()
        count += 1
        logger.info("Lead %d: Tier %s, heirs=%d", lead_id, final_tier, len(data.get("list_of_heirs", [])))
    
    return count
