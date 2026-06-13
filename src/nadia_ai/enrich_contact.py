"""Contact-discovery enrichment via a search-native LLM.

The pipeline's other LLM step (utils/extraction.py) answers *who* a lead is.
This step answers *how to reach them*: given an heir/causante name + city, it
queries a search-native model to find a contact path (phone, email, public
profile) and keeps the source citation.

Why not Claude here: Claude has no web index, so it cannot search. We use
Perplexity Sonar (primary) — purpose-built for web search, returns citations —
with optional Gemini-grounding as an alternate provider. The design mirrors
utils/extraction.py: each provider returns None to defer to the next.

Legality scope (see docs/LLM_AND_DATA_LEGALITY.md): only run on leads that are
`outreach_allowed`, have a valid person name, and carry an inheritance/legal
signal — not raw obituary mourners. Citations are retained so an identity match
can be verified and so first-contact can satisfy GDPR Art. 14 transparency.

Usage:
    python -m nadia_ai.enrich_contact
"""

import json
import logging
import re
import sqlite3
from datetime import UTC, datetime

import requests

from nadia_ai.config import (
    CONTACT_ENRICH_MAX_PER_RUN,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    PERPLEXITY_API_KEY,
    PERPLEXITY_API_URL,
    PERPLEXITY_MODEL,
)
from nadia_ai.db import get_connection
from nadia_ai.merge import init_leads_schema
from nadia_ai.utils.names import is_valid_person_name

logger = logging.getLogger("nadia_ai.enrich_contact")

# B2B sources have their own registry-based enrichment path; this module is for
# the citizen-heir population.
_B2B_SOURCES = ("traspasos", "borme_i", "borme_ii", "BORME-I", "BORME-II", "Traspasos")

SEARCH_PROMPT = """Eres un investigador OSINT que localiza datos de contacto PÚBLICOS de personas en España, usando únicamente fuentes abiertas (páginas blancas, registros públicos, redes sociales públicas, obituarios, notas de prensa).

Persona a localizar:
- Nombre: {name}
- Ciudad / localidad: {city}
- Contexto: {context}

Busca en la web española un posible dato de contacto de ESTA persona concreta (no de un homónimo). Devuelve SOLO un objeto JSON con esta forma exacta:

{{
  "identity_match": true/false,   // true solo si estás razonablemente seguro de que es la misma persona (nombre + localidad coinciden)
  "confidence": "high|medium|low",
  "phone": "teléfono público o null",
  "email": "email público o null",
  "profile_url": "URL de perfil público (LinkedIn/Facebook/web) o null",
  "reasoning": "una frase explicando en qué te basas"
}}

REGLAS:
1. Si no encuentras a la persona o no puedes distinguirla de homónimos, pon identity_match=false y todos los datos a null.
2. No inventes números ni emails. Si no hay fuente, es null.
3. Responde SOLAMENTE el JSON, sin texto adicional."""


def _parse_json_blob(text: str) -> dict | None:
    """Tolerant JSON extraction — search models often wrap JSON in prose/fences."""
    if not text:
        return None
    # Strip code fences if present
    text = re.sub(r"```(?:json)?", "", text).strip()
    # Grab the first {...} block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _search_via_perplexity(prompt: str) -> tuple[dict, list[str]] | None:
    """Query Perplexity Sonar. Returns (parsed_json, citations) or None to defer."""
    if not PERPLEXITY_API_KEY:
        return None
    try:
        resp = requests.post(
            PERPLEXITY_API_URL,
            headers={
                "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": PERPLEXITY_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            },
            timeout=40,
        )
        resp.raise_for_status()
        body = resp.json()
        content = body["choices"][0]["message"]["content"]
        data = _parse_json_blob(content)
        if data is None:
            logger.warning("Perplexity returned unparseable content; deferring")
            return None
        # Newer responses use `search_results`; older ones use `citations`.
        citations = body.get("citations") or [
            r.get("url") for r in body.get("search_results", []) if r.get("url")
        ]
        return data, citations
    except (requests.RequestException, KeyError, ValueError) as e:
        logger.warning("Perplexity search failed, deferring: %s", e)
        return None


def _search_via_gemini(prompt: str) -> tuple[dict, list[str]] | None:
    """Query Gemini with Google Search grounding. Alternate provider; returns
    (parsed_json, citations) or None to defer."""
    if not GEMINI_API_KEY:
        return None
    try:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
        )
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "tools": [{"google_search": {}}],
                "generationConfig": {"temperature": 0},
            },
            timeout=40,
        )
        resp.raise_for_status()
        body = resp.json()
        parts = body["candidates"][0]["content"]["parts"]
        content = "".join(p.get("text", "") for p in parts)
        data = _parse_json_blob(content)
        if data is None:
            return None
        # Grounding citations live under groundingMetadata.
        meta = body["candidates"][0].get("groundingMetadata", {})
        citations = [
            c.get("web", {}).get("uri")
            for c in meta.get("groundingChunks", [])
            if c.get("web", {}).get("uri")
        ]
        return data, citations
    except (requests.RequestException, KeyError, ValueError) as e:
        logger.warning("Gemini search failed, deferring: %s", e)
        return None


def discover_contact(name: str, city: str, context: str) -> dict | None:
    """Find a public contact path for a person. Returns a normalized dict with
    phone/email/profile_url/confidence/source_url, or None if no provider is
    configured or nothing usable was found.

    A returned dict with all-None contact fields means "searched, found nothing"
    (a real negative); None means "could not search" (transient/no key) so the
    caller can retry later.
    """
    if not is_valid_person_name(name):
        return {"identity_match": False, "confidence": "low", "phone": None,
                "email": None, "profile_url": None, "source_url": None}

    prompt = SEARCH_PROMPT.format(name=name, city=city or "España", context=context or "herencia")

    result = _search_via_perplexity(prompt) or _search_via_gemini(prompt)
    if result is None:
        return None  # no provider available / transient — let caller retry

    data, citations = result
    # Only trust contact details when the model is confident it's the same person.
    if not data.get("identity_match") or data.get("confidence") == "low":
        return {"identity_match": False, "confidence": data.get("confidence", "low"),
                "phone": None, "email": None, "profile_url": None, "source_url": None}

    return {
        "identity_match": True,
        "confidence": data.get("confidence", "medium"),
        "phone": (data.get("phone") or None),
        "email": (data.get("email") or None),
        "profile_url": (data.get("profile_url") or None),
        "source_url": citations[0] if citations else None,
    }


def get_leads_for_contact(conn: sqlite3.Connection, limit: int) -> list[dict]:
    """Tier A/B, outreach-allowed leads with a valid name and no contact yet."""
    init_leads_schema(conn)
    rows = conn.execute(
        """SELECT id, causante, heir_name, localidad, sources, tier, urgency_phase
           FROM leads
           WHERE tier IN ('A', 'B')
             AND outreach_allowed = 1
             AND (contact_enriched_at IS NULL OR contact_enriched_at = '')
             AND (
                 (heir_name IS NOT NULL AND heir_name != '')
                 OR (causante IS NOT NULL AND causante != '')
             )
           ORDER BY CASE tier WHEN 'A' THEN 0 ELSE 1 END,
                    days_since_death DESC NULLS LAST
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def run_contact_enrichment(conn: sqlite3.Connection, limit: int | None = None) -> int:
    """Discover contact paths for eligible leads. Returns the count with a contact found."""
    if not (PERPLEXITY_API_KEY or GEMINI_API_KEY):
        logger.warning(
            "No search provider configured (PERPLEXITY_API_KEY / GEMINI_API_KEY) — "
            "skipping contact enrichment"
        )
        return 0

    limit = limit if limit is not None else CONTACT_ENRICH_MAX_PER_RUN
    leads = get_leads_for_contact(conn, limit)
    if not leads:
        logger.info("No leads pending contact enrichment")
        return 0

    found = 0
    for lead in leads:
        # Prefer the living heir (whom we'd actually contact); fall back to causante.
        name = lead.get("heir_name") or lead.get("causante") or ""
        # B2B leads are handled elsewhere — skip if the only signal is a company.
        sources = lead.get("sources") or ""
        if not lead.get("heir_name") and any(b in sources for b in _B2B_SOURCES):
            continue

        result = discover_contact(name, lead.get("localidad") or "", "herencia / sucesión")
        if result is None:
            # Provider transient/unavailable — leave unmarked so it retries next run.
            logger.info("Lead %d: search unavailable, will retry", lead["id"])
            continue

        has_contact = bool(result.get("phone") or result.get("email") or result.get("profile_url"))
        conn.execute(
            """UPDATE leads SET
                contact_phone = ?,
                contact_email = ?,
                contact_profile_url = ?,
                contact_source_url = ?,
                contact_confidence = ?,
                contact_enriched_at = ?,
                social_profile_url = COALESCE(NULLIF(?, ''), social_profile_url),
                last_updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (
                result.get("phone") or "",
                result.get("email") or "",
                result.get("profile_url") or "",
                result.get("source_url") or "",
                result.get("confidence") or "",
                datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
                result.get("profile_url") or "",
                lead["id"],
            ),
        )
        conn.commit()
        if has_contact:
            found += 1
            logger.info("Lead %d: contact found (%s)", lead["id"], result.get("confidence"))

    logger.info("Contact enrichment: %d/%d leads got a contact path", found, len(leads))
    return found


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(name)s — %(message)s")
    conn = get_connection()
    run_contact_enrichment(conn)
    conn.close()


if __name__ == "__main__":
    main()
