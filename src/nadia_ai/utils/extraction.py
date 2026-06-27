import json
import logging
import re
import sqlite3
import os
import time
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
  "property_address": "Dirección a nivel CALLE Y NÚMERO del inmueble relevante: el último domicilio/residencia del fallecido O la finca/inmueble objeto de la herencia (p.ej. 'finca sita en Calle Mayor 23', 'inmueble sito en Avenida de Goya 45'). Incluye SIEMPRE el número de la calle si aparece. Si solo se menciona la ciudad ('vecino de [Ciudad]'), pon la ciudad. NUNCA uses direcciones de tanatorios, notarías, juzgados ni registros.",
  "referencia_catastral": "Ref. Catastral si aparece (20 caracteres)."
}}

REGLAS CRÍTICAS:
1. Si el texto es una esquela, busca los nombres de los hijos (ej: 'Sus hijos: Juan y María').
2. Diferencia entre el lugar del fallecimiento (hospital/tanatorio) y el domicilio o vecindad (ej: 'vecino de Zaragoza').
3. property_address: prioriza calle + número sobre solo la ciudad. En edictos judiciales de herencia yacente, ese inmueble suele ser la finca del caudal hereditario ("finca/inmueble/vivienda sita en…"), que es un dato válido.
4. Responde SOLAMENTE el JSON.

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

def _parse_json_response(content: str) -> dict | None:
    """Tolerant parse — models wrap JSON in ```json fences and/or trailing prose."""
    if not content:
        return None
    cleaned = re.sub(r"```(?:json)?", "", content).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


def _extract_via_llm(prompt: str) -> dict | None:
    """Extract via the configured OpenAI-compatible extraction model. Works in CI.
    Returns the raw 5-key dict, or None to defer to regex.

    Use a NON-reasoning model (default: deepseek/deepseek-chat on OpenRouter). A
    reasoning model (e.g. MiniMax M3) spends its output-token budget "thinking" and
    returns EMPTY content on long edict bodies (judicial BOE-J texts up to 10k
    chars), which silently killed extraction once BOE became reachable. A plain
    chat model emits the JSON directly.

    We deliberately do NOT send response_format={"type":"json_object"}: the tolerant
    parser already handles fenced / trailing-prose JSON, and that param caused empty
    bodies with some providers. One short retry covers a rare transient empty."""
    from nadia_ai.config import EXTRACTION_API_KEY, EXTRACTION_API_URL, EXTRACTION_MODEL
    if not EXTRACTION_API_KEY:
        return None
    headers = {
        "Authorization": f"Bearer {EXTRACTION_API_KEY}",
        "Content-Type": "application/json",
    }
    # OpenRouter reserves the full max_tokens worth of credits up front and 402s if
    # that reservation exceeds the affordable balance. The extraction JSON is short,
    # so 1500 is ample for a non-reasoning model; we HALVE on a max_tokens 402 so it
    # self-heals as the balance shrinks instead of dying.
    max_tokens = 1500
    attempts = 2  # 1 try + 1 retry — a non-reasoning model rarely returns empty
    for attempt in range(attempts):
        payload = {
            "model": EXTRACTION_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        try:
            resp = requests.post(EXTRACTION_API_URL, headers=headers, json=payload, timeout=90)
            if resp.status_code == 402:
                body = resp.text.lower()
                if "max_tokens" in body:
                    # Adaptive: the affordability/reservation cap — halve and retry.
                    new_mt = max(500, max_tokens // 2)
                    logger.warning(
                        "Extraction 402 max_tokens cap; shrinking %d -> %d and retrying",
                        max_tokens, new_mt,
                    )
                    if new_mt == max_tokens:
                        return None
                    max_tokens = new_mt
                    continue
                # Definitive billing block ("insufficient credits" / never purchased):
                # retrying only wastes calls. Stop immediately on the first one.
                logger.error(
                    "Extraction LLM 402 (account billing / out of credits): %s — "
                    "stopping, no retry. Purchase OpenRouter credits to resume.",
                    resp.text[:200],
                )
                return None
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"].get("content") or ""
            parsed = _parse_json_response(content)
            if parsed is not None:
                return parsed
            logger.info(
                "Extraction LLM returned empty/garbled body (attempt %d/%d), retrying",
                attempt + 1, attempts,
            )
        except Exception as e:
            logger.warning(
                "LLM extraction call failed (attempt %d/%d): %s", attempt + 1, attempts, e
            )
    return None


def extract_inheritance_data(text: str, causante_hint: str = None) -> dict:
    """Extract inheritance details with the configured LLM (if EXTRACTION_API_KEY
    is set), else regex. The downstream sanitization is identical for both."""
    try:
        cleaned_text = clean_legal_text(text)
        truncated_text = cleaned_text[:10000]

        prompt = f"El fallecido es: {causante_hint}\n\n" + EXTRACTION_PROMPT.format(text=truncated_text)

        # Configured cloud extraction model if a key is set, else regex.
        from nadia_ai.config import EXTRACTION_API_KEY
        result = _extract_via_llm(prompt)
        if result is None:
            if EXTRACTION_API_KEY:
                # The LLM IS configured but this call failed (402 / timeout / etc).
                # Do NOT fall back to regex: regex returns a single guessed heir and
                # the caller would WRITE it over good prior data and mark the lead
                # done — exactly the degradation seen on the 2026-06-14 OpenRouter
                # 402 cascade. Return {} so the caller defers (leaves it pending).
                logger.info("Extraction LLM unavailable for this lead — deferring (no regex clobber).")
                return {}
            logger.info("No LLM configured — using regex extraction.")
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

        # Drop self-references. An heir equal to the DECEASED is a common,
        # nonsensical LLM error; the notary/court is already filtered by
        # sanitize() (notar/juzgado/registro keywords). Compare on a normalized
        # (accent/case/space-folded) full name so "JUAN PÉREZ" == "Juan Perez".
        from nadia_ai.merge import strip_accents

        def _norm_name(s):
            return " ".join(strip_accents(str(s)).lower().split()) if s else ""

        _dead = {_norm_name(causante_hint), _norm_name(result.get("deceased_name"))} - {""}
        if _dead:
            heirs = [h for h in heirs if _norm_name(h) not in _dead]

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

def _extract_lead_payload(lead: dict) -> tuple:
    """Network-only worker for one lead — NO database access, so it is safe to run
    in a thread pool (SQLite connections are not thread-safe). Does the fetch +
    deterministic gate + LLM extraction + Catastro RC validation and returns an
    action tuple the main thread applies serially:

      ("done", id)   → mark extraction done (obituary-only, or no fetchable text)
      ("cold", id)   → mark done + Tier C (failed the inheritance keyword gate)
      ("defer", id)  → leave pending (LLM unavailable for this lead)
      ("write", id, payload) → write the extracted fields
    """
    lead_id = lead["id"]
    causante = lead["causante"]
    urls = json.loads(lead["source_urls"] or "[]")

    # Obituary-only sources are a death SIGNAL, not a heir document (~0% yield).
    sources_list = json.loads(lead["sources"] or "[]")
    if sources_list and all(
        s in ("Esquelas", "Defunciones", "iEsquelas", "rememori") for s in sources_list
    ):
        return ("done", lead_id)

    # 1. Fetch full text
    full_text = ""
    for url in urls:
        fetch_url = url
        if "zaragoza.es/sede/servicio/tablon-edicto/" in url and not url.endswith("/document"):
            fetch_url = url + "/document"
        if "boe.es" in fetch_url:
            full_text = extract_pdf_text(fetch_url)
        else:
            try:
                r = requests.get(fetch_url, timeout=15)
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, "html.parser")
                    main = soup.select_one(".content, #main, article, .esquela-detalle, .texto-esquela")
                    full_text = main.get_text(separator=" ", strip=True) if main else soup.get_text(separator=" ", strip=True)
            except Exception as e:
                logger.warning("Failed to fetch text from %s: %s", fetch_url, e)
        if full_text:
            break

    if not full_text:
        return ("done", lead_id)  # no fetchable text — stop re-queueing it
    if not is_worth_llm_compute(full_text):
        return ("cold", lead_id)  # no inheritance keywords

    data = extract_inheritance_data(full_text, causante_hint=causante)
    if not data:
        return ("defer", lead_id)  # LLM unavailable — leave pending for retry

    # Trust but verify: validate any RC via Catastro (network, no DB).
    from nadia_ai.catastro import lookup_by_rc
    rc = data.get("referencia_catastral")
    if rc:
        parcel = lookup_by_rc(rc)
        if not parcel:
            data["referencia_catastral"] = None
            data["property_address"] = None
        elif parcel.address:
            data["property_address"] = parcel.address

    # Tier A = mailable: street number or RC (consistent with merge.compute_tier).
    _pa = data.get("property_address") or ""
    final_tier = "A" if (re.search(r"\d", _pa) or data.get("referencia_catastral")) else "B"

    heirs_list = data.get("list_of_heirs", [])
    if not isinstance(heirs_list, list):
        heirs_list = [str(heirs_list)]
    heirs_list = clean_name_list(heirs_list)

    death_date = data.get("date_of_death")
    if isinstance(death_date, list):
        death_date = death_date[0] if death_date else None
    death_date = str(death_date) if death_date else None

    prop_addr = data.get("property_address")
    rc_val = data.get("referencia_catastral")
    from nadia_ai.merge import normalize_address, normalize_name
    extracted_deceased = (data.get("deceased_name") or "").strip() or None
    final_causante = (causante or "").strip() or extracted_deceased

    return ("write", lead_id, {
        "heirs_json": json.dumps(heirs_list),
        "primary_heir": heirs_list[0] if heirs_list else None,
        "final_causante": final_causante,
        "final_causante_norm": normalize_name(final_causante) if final_causante else None,
        "prop_addr": prop_addr,
        "addr_norm": normalize_address(prop_addr) if prop_addr else None,
        "rc_val": rc_val,
        "death_date": death_date,
        "final_tier": final_tier,
        "n_heirs": len(heirs_list),
    })


def run_heir_extraction(conn, limit: int = 200, extra_where: str = "", deadline: float | None = None):
    """Enrich pending leads by extracting heirs and addresses from their source documents.

    Includes Catastro validation as a gatekeeper for Tier A.

    `limit` caps how many leads are processed; `extra_where` is an optional extra
    SQL filter (e.g. a province + source scope) ANDed onto the pending-leads query
    for targeted/pilot runs. `deadline` is an optional `time.monotonic()` cutoff:
    each lead can cost tens of seconds (a slow/garbled LLM retries up to 4x), so the
    loop stops cleanly once past it, leaving the rest pending for the next run. This
    is what keeps a slow LLM from starving the worklist email at the end of the run.
    """
    from nadia_ai.catastro import lookup_by_rc
    from nadia_ai.config import EXTRACTION_API_KEY
    conn.row_factory = sqlite3.Row

    # Preflight guard: if an extraction LLM IS configured but unreachable (402 out
    # of credits / auth / network), abort. Otherwise every lead would fall back to
    # the regex extractor, which OVERWRITES good multi-heir LLM data with a single
    # guessed name AND marks the lead done — silently degrading the data during a
    # transient outage (this exact loss happened 2026-06-14 on an OpenRouter 402).
    # Leaving leads pending lets a clean retry recover them once the LLM is back.
    # (No key configured = regex is the intended path, so we don't preflight.)
    if EXTRACTION_API_KEY and _extract_via_llm('Responde solo: {"ok": true}') is None:
        logger.error(
            "Extraction LLM is configured but unreachable (out of credits / auth / "
            "network). Aborting heir extraction to avoid regex clobbering good data; "
            "leads left pending for retry."
        )
        return 0

    where = "ai_extraction_done = 0"
    if extra_where:
        where += f" AND ({extra_where})"
    cursor = conn.execute(f"""
        SELECT id, causante, source_urls, tier, sources
        FROM leads
        WHERE {where}
        ORDER BY
            CASE
                WHEN sources LIKE '%BOE%' OR sources LIKE '%Tablón%' OR sources LIKE '%BOA%' OR sources LIKE '%BOP%'
                  OR sources LIKE '%BOCM%' OR sources LIKE '%DOGC%' OR sources LIKE '%BOJA%' OR sources LIKE '%DOGV%' THEN 0
                ELSE 1
            END ASC,
            tier ASC,
            first_seen_at DESC
        LIMIT {int(limit)}
    """)
    leads = cursor.fetchall()
    
    count = 0

    def _apply(action: tuple) -> None:
        """Apply one worker's result to the DB. Main-thread only (SQLite serial)."""
        nonlocal count
        kind, lead_id = action[0], action[1]
        if kind == "done":
            conn.execute("UPDATE leads SET ai_extraction_done = 1 WHERE id = ?", (lead_id,))
            conn.commit()
        elif kind == "cold":
            conn.execute("UPDATE leads SET ai_extraction_done = 1, tier = 'C' WHERE id = ?", (lead_id,))
            conn.commit()
        elif kind == "defer":
            pass  # LLM unavailable — leave pending, no clobber
        elif kind == "write":
            p = action[2]
            conn.execute("""
                UPDATE leads
                SET heir_names_json = ?, heir_name = ?, causante = ?,
                    causante_norm = COALESCE(?, causante_norm),
                    direccion = ?, address_norm = COALESCE(?, address_norm),
                    referencia_catastral = ?,
                    ref_catastral = COALESCE(NULLIF(?, ''), ref_catastral),
                    date_of_death = ?, tier = ?, ai_extraction_done = 1,
                    last_updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                p["heirs_json"], p["primary_heir"], p["final_causante"],
                p["final_causante_norm"], p["prop_addr"], p["addr_norm"],
                p["rc_val"], p["rc_val"] or "", p["death_date"], p["final_tier"], lead_id,
            ))
            conn.commit()
            count += 1
            logger.info("Lead %d: Tier %s, heirs=%d", lead_id, p["final_tier"], p["n_heirs"])

    # Parallelise the network-bound work (fetch + LLM + Catastro) across a thread
    # pool — it's ~all of the run's wall-clock — while keeping every DB write on the
    # main thread (SQLite connections aren't thread-safe). EXTRACTION_WORKERS=1 keeps
    # the old sequential behaviour. Workers only read the row dict passed to them.
    workers = max(1, int(os.getenv("EXTRACTION_WORKERS", "6")))
    lead_dicts = [dict(lead) for lead in leads]

    if workers == 1:
        for ld in lead_dicts:
            if deadline is not None and time.monotonic() > deadline:
                logger.warning("Heir extraction hit the time budget — stopping; remaining pending.")
                break
            _apply(_extract_lead_payload(ld))
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_extract_lead_payload, ld): ld["id"] for ld in lead_dicts}
            for fut in as_completed(futures):
                if deadline is not None and time.monotonic() > deadline:
                    logger.warning("Heir extraction hit the time budget — stopping; remaining pending.")
                    for f in futures:
                        f.cancel()
                    break
                try:
                    _apply(fut.result())
                except Exception as e:
                    logger.warning("Lead extraction worker failed: %s", e)

    logger.info("Heir extraction: %d leads enriched (workers=%d)", count, workers)
    return count
