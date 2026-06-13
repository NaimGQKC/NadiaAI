"""Contact-discovery enrichment via a search-native LLM.

The pipeline's other LLM step (utils/extraction.py) answers *who* a lead is.
This step answers *how to reach them*: given an heir/causante name + city, it
queries a search-native model to find a contact path (phone, email, public
profile) and keeps the source citation.

Search provider: Perplexity Sonar — purpose-built for web search, returns the
citations we keep for identity verification. A search-native model is required
here (a plain chat model has no web index).

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
from dataclasses import dataclass
from datetime import UTC, datetime

import requests

from nadia_ai.config import (
    CONTACT_ENRICH_MAX_PER_RUN,
    EINFORMA_API_KEY,
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

    result = _search_via_perplexity(prompt)
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


def get_leads_for_contact(conn: sqlite3.Connection, limit: int, extra_where: str = "") -> list[dict]:
    """Tier A/B, outreach-allowed leads with a valid name and no contact yet.

    `extra_where` is an optional extra SQL filter ANDed on for targeted runs
    (e.g. "localidad LIKE '%aragoza%' AND heir_name != ''")."""
    init_leads_schema(conn)
    where = (
        "tier IN ('A', 'B') AND outreach_allowed = 1"
        " AND (contact_enriched_at IS NULL OR contact_enriched_at = '')"
        " AND ((heir_name IS NOT NULL AND heir_name != '')"
        "      OR (causante IS NOT NULL AND causante != ''))"
    )
    if extra_where:
        where += f" AND ({extra_where})"
    rows = conn.execute(
        f"""SELECT id, causante, heir_name, localidad, sources, tier, urgency_phase
           FROM leads
           WHERE {where}
           ORDER BY CASE tier WHEN 'A' THEN 0 ELSE 1 END,
                    days_since_death DESC NULLS LAST
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


# ── Waterfall enrichment ───────────────────────────────────────────────────
# Industry-standard pattern (Clay/Apollo/FullEnrich): try sources in priority
# order, cheapest/highest-precision first, stop on the first verified hit, and
# record which source supplied it. Each source self-selects (returns None when it
# doesn't apply — wrong lead type, not configured, or transient) so adding a new
# provider later is a one-line entry in CONTACT_SOURCES.


@dataclass
class ContactResult:
    """A waterfall source's answer. None (not this object) means 'defer to next'."""
    source: str            # which tier answered ('einforma', 'search_llm', 'none')
    has_contact: bool
    phone: str | None = None
    email: str | None = None
    profile_url: str | None = None
    source_url: str | None = None
    confidence: str = ""


def _is_b2b_lead(lead: dict) -> bool:
    """A company lead (borme/traspasos with no named heir) — a registry job, not
    a person to web-search."""
    sources = lead.get("sources") or ""
    return not lead.get("heir_name") and any(b in sources for b in _B2B_SOURCES)


def _source_einforma(lead: dict, name: str, city: str, context: str) -> ContactResult | None:
    """TIER 1 — Spanish company registry (eInforma/Axesor). The closest thing to a
    US skip-trace API that works in Spain, but B2B only. Paid: stub until
    EINFORMA_API_KEY + an implementation are added."""
    if not EINFORMA_API_KEY or not _is_b2b_lead(lead):
        return None
    # TODO: call eInforma, return ContactResult(source="einforma", has_contact=...).
    return None


def _source_paginas_blancas(lead: dict, name: str, city: str, context: str) -> ContactResult | None:
    """TIER 2 — free public phone directory (paginasblancas.es). Best for older
    heirs with a listed landline; zero cost so it runs before any paid search.
    Stub: implement a directory lookup that returns ContactResult or None."""
    if _is_b2b_lead(lead):
        return None
    # TODO: implement free directory lookup (no key required).
    return None


def _source_search_llm(lead: dict, name: str, city: str, context: str) -> ContactResult | None:
    """TIER 3 — search-native LLM (Perplexity Sonar). The pragmatic substitute for
    the Spanish skip-trace broker that doesn't exist."""
    if _is_b2b_lead(lead):
        return None
    res = discover_contact(name, city, context)
    if res is None:
        return None  # provider unavailable / transient → defer (retry next run)
    has = bool(res.get("phone") or res.get("email") or res.get("profile_url"))
    return ContactResult(
        source="search_llm" if has else "none",
        has_contact=has,
        phone=res.get("phone"),
        email=res.get("email"),
        profile_url=res.get("profile_url"),
        source_url=res.get("source_url"),
        confidence=res.get("confidence", ""),
    )


# Priority order: cheapest / highest-precision first.
CONTACT_SOURCES = [
    ("einforma", _source_einforma),
    ("paginas_blancas", _source_paginas_blancas),
    ("search_llm", _source_search_llm),
]


def enrich_lead(lead: dict) -> ContactResult | None:
    """Run the waterfall for one lead.

    Returns a ContactResult on a positive hit, a negative ContactResult
    (source='none') if at least one source ran but found nothing (so the lead is
    marked done, not re-queued), or None if no source could run at all (transient
    / unconfigured → leave it for a retry).
    """
    name = lead.get("heir_name") or lead.get("causante") or ""
    city = lead.get("localidad") or ""
    context = "herencia / sucesión"

    ran_any = False
    for _source_name, fn in CONTACT_SOURCES:
        result = fn(lead, name, city, context)
        if result is None:
            continue  # source deferred
        ran_any = True
        if result.has_contact:
            return result  # verified hit — stop the waterfall
        # ran but found nothing → keep cascading to the next source
    return ContactResult(source="none", has_contact=False) if ran_any else None


def run_contact_enrichment(conn: sqlite3.Connection, limit: int | None = None, extra_where: str = "") -> int:
    """Run the contact waterfall over eligible leads. Returns the count with a
    contact found. `extra_where` scopes the candidate query for targeted runs."""
    if not (PERPLEXITY_API_KEY or EINFORMA_API_KEY):
        logger.warning(
            "No enrichment source configured (PERPLEXITY_API_KEY / EINFORMA_API_KEY) "
            "— skipping contact enrichment"
        )
        return 0

    limit = limit if limit is not None else CONTACT_ENRICH_MAX_PER_RUN
    leads = get_leads_for_contact(conn, limit, extra_where)
    if not leads:
        logger.info("No leads pending contact enrichment")
        return 0

    found = 0
    for lead in leads:
        result = enrich_lead(lead)
        if result is None:
            # No source could run for this lead — leave unmarked so it retries.
            continue

        conn.execute(
            """UPDATE leads SET
                contact_phone = ?,
                contact_email = ?,
                contact_profile_url = ?,
                contact_source_url = ?,
                contact_confidence = ?,
                contact_source = ?,
                contact_enriched_at = ?,
                social_profile_url = COALESCE(NULLIF(?, ''), social_profile_url),
                last_updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (
                result.phone or "",
                result.email or "",
                result.profile_url or "",
                result.source_url or "",
                result.confidence or "",
                result.source or "",
                datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
                result.profile_url or "",
                lead["id"],
            ),
        )
        conn.commit()
        if result.has_contact:
            found += 1
            logger.info("Lead %d: contact via %s (%s)", lead["id"], result.source, result.confidence)

    logger.info("Contact enrichment: %d/%d leads got a contact path", found, len(leads))
    return found


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(name)s — %(message)s")
    conn = get_connection()
    run_contact_enrichment(conn)
    conn.close()


if __name__ == "__main__":
    main()
