"""LLM-based extraction of inheritance data from raw edict/obituary text.

Uses local Ollama to parse unstructured Spanish legal text and extract:
- Deceased name (causante)
- Date of death (fecha de fallecimiento)
- List of heirs (herederos)
- Property address (if present)

Falls back to regex patterns when Ollama is unavailable.
"""

import json
import logging
import re
import sqlite3
from datetime import UTC, datetime

import requests

from nadia_ai.config import OLLAMA_MODEL, OLLAMA_URL

logger = logging.getLogger("nadia_ai.utils.extraction")

OLLAMA_GENERATE_URL = f"{OLLAMA_URL}/api/generate"

# Prompt tuned for Spanish legal edict text
EXTRACTION_PROMPT = """Eres un asistente legal especializado en herencias españolas.
Analiza el siguiente texto de un edicto o esquela y extrae los datos en formato JSON.

IMPORTANTE: Responde SOLO con el JSON, sin texto adicional.

Formato de respuesta:
{{
  "deceased_name": "nombre completo del fallecido/causante",
  "date_of_death": "YYYY-MM-DD o null si no se menciona",
  "list_of_heirs": ["nombre heredero 1", "nombre heredero 2"],
  "property_address": "dirección del inmueble o null si no se menciona"
}}

Texto a analizar:
{text}"""

# Regex fallback patterns for when Ollama is unavailable
_HEIR_PATTERNS = [
    # "herederos: D./Dña. NAME, D./Dña. NAME"
    re.compile(
        r"herederos?[:\s]+(?:(?:D\.?[aª]?\s+|D[ñÑ]a\.?\s+|[Dd]o[nñ]a?\s+)"
        r"([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑa-záéíóúñ][a-záéíóúñA-ZÁÉÍÓÚÑ]+){1,4}))",
        re.IGNORECASE,
    ),
    # "hijos/as: NAME y NAME"
    re.compile(
        r"hij[oa]s?[:\s]+(?:D\.?[aª]?\s+|D[ñÑ]a\.?\s+|[Dd]o[nñ]a?\s+)?"
        r"([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑa-záéíóúñ][a-záéíóúñA-ZÁÉÍÓÚÑ]+){1,4})",
        re.IGNORECASE,
    ),
    # "solicitado por D./Dña. NAME"
    re.compile(
        r"solicitad[oa]\s+por\s+(?:D\.?[aª]?\s+|D[ñÑ]a\.?\s+|[Dd]o[nñ]a?\s+)"
        r"([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑa-záéíóúñ][a-záéíóúñA-ZÁÉÍÓÚÑ]+){1,4})",
        re.IGNORECASE,
    ),
]


def _call_ollama(text: str) -> dict | None:
    """Call local Ollama and parse the JSON response."""
    prompt = EXTRACTION_PROMPT.format(text=text[:3000])  # Cap input to ~3k chars
    try:
        resp = requests.post(
            OLLAMA_GENERATE_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 500},
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        raw_response = data.get("response", "")

        # Try to extract JSON from the response
        # LLMs sometimes wrap JSON in markdown code blocks
        json_match = re.search(r"\{[^{}]*\}", raw_response, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(0))

        # Try parsing the whole response as JSON
        return json.loads(raw_response)

    except requests.ConnectionError:
        logger.warning("Ollama not available at %s — using regex fallback", OLLAMA_URL)
        return None
    except requests.Timeout:
        logger.warning("Ollama timed out — using regex fallback")
        return None
    except (requests.RequestException, json.JSONDecodeError, ValueError) as e:
        logger.warning("Ollama extraction failed: %s — using regex fallback", e)
        return None


def _regex_fallback(text: str) -> dict:
    """Extract heir names using regex patterns when Ollama is unavailable."""
    heirs = []
    for pattern in _HEIR_PATTERNS:
        for match in pattern.finditer(text):
            name = match.group(1).strip()
            if name.isupper():
                name = name.title()
            if name and name not in heirs and len(name) > 3:
                heirs.append(name)

    return {
        "deceased_name": None,
        "date_of_death": None,
        "list_of_heirs": heirs,
        "property_address": None,
    }


def extract_inheritance_data(raw_text: str) -> dict:
    """Extract structured inheritance data from raw edict/obituary text.

    Returns:
        {
            "deceased_name": str or None,
            "date_of_death": str (ISO) or None,
            "list_of_heirs": list[str],
            "property_address": str or None,
        }
    """
    if not raw_text or len(raw_text.strip()) < 20:
        return {
            "deceased_name": None,
            "date_of_death": None,
            "list_of_heirs": [],
            "property_address": None,
        }

    # Try Ollama first
    result = _call_ollama(raw_text)

    if result:
        # Sanitize the LLM output
        heirs = result.get("list_of_heirs", [])
        if isinstance(heirs, str):
            heirs = [h.strip() for h in heirs.split(",") if h.strip()]
        return {
            "deceased_name": result.get("deceased_name"),
            "date_of_death": result.get("date_of_death"),
            "list_of_heirs": heirs or [],
            "property_address": result.get("property_address"),
        }

    # Fallback to regex
    return _regex_fallback(raw_text)


def run_heir_extraction(conn: sqlite3.Connection) -> int:
    """Run LLM heir extraction for leads that don't have heirs yet.

    Queries leads with empty heir_names_json, fetches their linked edict
    text, runs extraction, and updates the lead.

    Returns count of leads enriched.
    """
    # Find leads without heirs that have linked edicts
    rows = conn.execute(
        """SELECT l.id, l.causante, l.direccion, l.fecha_fallecimiento,
                  e.source_url, e.address
           FROM leads l
           LEFT JOIN lead_edicts le ON l.id = le.lead_id
           LEFT JOIN edicts e ON le.edict_id = e.id
           WHERE (l.heir_names_json = '[]' OR l.heir_names_json IS NULL)
             AND l.tier IN ('A', 'B')
           GROUP BY l.id
           LIMIT 50"""
    ).fetchall()

    if not rows:
        logger.info("No leads pending heir extraction")
        return 0

    enriched = 0
    for row in rows:
        # Build context text from available data
        context_parts = []
        if row["causante"]:
            context_parts.append(f"Causante: {row['causante']}")
        if row["direccion"]:
            context_parts.append(f"Dirección: {row['direccion']}")
        if row["fecha_fallecimiento"]:
            context_parts.append(f"Fecha fallecimiento: {row['fecha_fallecimiento']}")

        context = " ".join(context_parts)
        if len(context) < 20:
            continue

        result = extract_inheritance_data(context)
        heirs = result.get("list_of_heirs", [])
        dod = result.get("date_of_death")

        if heirs or dod:
            updates = []
            params = []

            if heirs:
                updates.append("heir_names_json = ?")
                params.append(json.dumps(heirs))
                updates.append("heir_name = COALESCE(NULLIF(heir_name, ''), ?)")
                params.append(heirs[0])

            if dod:
                updates.append("date_of_death = COALESCE(NULLIF(date_of_death, ''), ?)")
                params.append(dod)

            updates.append("last_updated_at = datetime('now')")
            params.append(row["id"])

            conn.execute(
                f"UPDATE leads SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            enriched += 1
            logger.info(
                "Lead %d: extracted %d heirs%s",
                row["id"],
                len(heirs),
                f", dod={dod}" if dod else "",
            )

    conn.commit()
    logger.info("LLM heir extraction complete: %d leads enriched", enriched)
    return enriched
