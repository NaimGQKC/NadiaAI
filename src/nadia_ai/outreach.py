"""AI-outreach layer — turn a lead into ready-to-send Spanish contact copy.

The data is a commodity; conversion is the moat. This module turns each lead into
a *worked* action: a phone script + WhatsApp for FSBO owners, a respectful postal
letter for inheritance heirs, a gentle delayed note for the recently bereaved —
the right channel and tone for each, so the agent opens the worklist and acts.

Design:
  * **Per-lead routing** — FSBO / notarial / judicial / obituary each get a
    distinct channel and register (a hot sales call vs a condolence-aware letter).
  * **Privacy by design** — the optional LLM only ever polishes a *template with
    {placeholders}*; the real causante/heir/address are filled LOCALLY, so no
    personal data leaves the machine. This product lives or dies on GDPR hygiene.
  * **Deterministic core, LLM is an upgrade** — hand-written templates always work
    (instant, free, offline). The LLM (provider-neutral, OpenAI-compatible, cached
    per type ≈ 4 calls) just sharpens the prose; if it's down, nothing breaks.
"""

from __future__ import annotations

import json
import logging
import re

import requests

logger = logging.getLogger("nadia_ai.outreach")


# ── Lead-type routing ────────────────────────────────────────────────────────

_NOTARIAL_SUBS = {"BOE-N", "Tablón", "Tabl�n", "BOA-JD"}
_JUDICIAL_SUBS = {"BOE-TEJU", "BOE-V.B"}


def lead_type(lead: dict) -> str:
    """Classify a lead into an outreach archetype: fsbo|notarial|judicial|obituary."""
    src = (lead.get("source") or "").lower()
    sub = lead.get("subsource") or ""
    sources = lead.get("sources") or ""
    if src == "fsbo_pisos" or sub == "FSBO":
        return "fsbo"
    if sub in _NOTARIAL_SUBS:
        return "notarial"
    if sub in _JUDICIAL_SUBS:
        return "judicial"
    # Heraldo/Memora obituaries (no legal subsource) — sensitive, delayed contact.
    if "Heraldo" in sources or sub in ("Esquelas", "Defunciones", "iEsquelas", "rememori"):
        return "obituary"
    # Default to the most cautious channel.
    return "obituary"


TIPO_LABEL = {
    "fsbo": "FSBO (particular en venta)",
    "notarial": "Herencia · notarial",
    "judicial": "Herencia · judicial",
    "obituary": "Obituario / esquela",
}


# ── Deterministic templates (Spanish, RE/MAX captación, RGPD-aware) ──────────
# Placeholders ({agent}, {agency}, {phone}, {causante}, {localidad}, {barrio},
# {precio}, {notaria}, {familia}) are filled locally in render_outreach().

TEMPLATES: dict[str, dict] = {
    "fsbo": {
        "canal": "Llamada + WhatsApp",
        "guion_llamada": (
            "Buenos días, ¿hablo con el propietario del inmueble en {barrio}? "
            "Le llamo porque he visto que lo vende como particular (pisos.com), a {precio}. "
            "Soy {agent}, de {agency}, y trabajo precisamente la zona de {localidad}: "
            "tengo compradores buscando en {barrio} ahora mismo. "
            "¿Le vendría bien que le explique, sin compromiso, cómo puedo ayudarle a venderlo "
            "más rápido y al mejor precio? No le cobro nada hasta que la venta se cierre."
        ),
        "mensaje": (
            "Hola, buenos días. He visto su anuncio del inmueble en {barrio} ({precio}) en "
            "pisos.com. Soy {agent}, agente de {agency} en {localidad}. Trabajo esa zona y tengo "
            "clientes interesados. ¿Le encajaría que le ayude a venderlo, sin compromiso y sin "
            "coste hasta la venta? Un saludo — {phone}"
        ),
        "asunto": "",
        "notas": (
            "Lead CALIENTE: el dueño ya quiere vender y tienes su teléfono directo. Llama pronto "
            "(quien contacta primero suele llevarse el mandato). Aporta valor —compradores, precio, "
            "gestión—, no presiones. Si no contesta, prueba el WhatsApp."
        ),
    },
    "notarial": {
        "canal": "Carta postal",
        "guion_llamada": "",
        "mensaje": (
            "Estimada familia:\n\n"
            "Me dirijo a ustedes con el mayor respeto. He tenido conocimiento, a través de la "
            "publicación oficial en el BOE, de la tramitación de la declaración de herederos de "
            "D./Dª {causante}.\n\n"
            "Soy {agent}, agente inmobiliario de {agency} en {localidad}. Sé que gestionar una "
            "herencia es un proceso delicado y lleno de trámites. Si entre los bienes hubiera una "
            "vivienda u otro inmueble que estén valorando vender, me pongo a su disposición para "
            "ofrecerles una valoración gratuita y, si lo desean, encargarme de toda la gestión de "
            "la venta, sin compromiso alguno.\n\n"
            "Quedo a su entera disposición en el teléfono {phone}.\n\n"
            "Un cordial saludo,\n{agent} — {agency}\n\n"
            "(En cumplimiento del RGPD: sus datos proceden de una fuente de acceso público (BOE). "
            "Puede solicitar su supresión respondiendo a esta carta.)"
        ),
        "asunto": "Ayuda profesional con la venta de un inmueble heredado",
        "notas": (
            "Canal: carta postal al último domicilio (más respetuoso y legal que llamar en frío). "
            "La notaría «{notaria}» es tu referencia para verificar el caso, NO para captar. "
            "Idealmente confirma que hay vivienda (Catastro) antes de invertir en el envío."
        ),
    },
    "judicial": {
        "canal": "Investigar / vigilar",
        "guion_llamada": "",
        "mensaje": (
            "Estimada familia:\n\n"
            "Con el debido respeto, le escribo en relación con el procedimiento de herencia "
            "{procedimiento} relativo a D./Dª {causante}, del que he tenido conocimiento por la "
            "publicación oficial (BOE). Soy {agent}, de {agency} en {localidad}. Si hubiera un "
            "inmueble que en su momento deseen vender, quedo a su disposición para una valoración "
            "sin compromiso.\n\n{agent} — {agency} — {phone}\n\n"
            "(Datos de fuente pública (BOE); puede solicitar su supresión.)"
        ),
        "asunto": "Inmueble en proceso de herencia — {localidad}",
        "notas": (
            "Herencia yacente: herederos desconocidos/ausentes — no hay a quién llamar todavía. "
            "Úsalo para VIGILAR: localiza el inmueble en Catastro y posiciónate. Cuando aparezca "
            "el edicto notarial de herederos, pasa al guion de herencia notarial."
        ),
    },
    "obituary": {
        "canal": "Carta (esperar 30-60 días)",
        "guion_llamada": "",
        "mensaje": (
            "Estimada familia {familia}:\n\n"
            "Reciban mis más sinceras condolencias por el fallecimiento de {causante}.\n\n"
            "Me presento por si en el futuro les pudiera ser de ayuda: soy {agent}, agente "
            "inmobiliario de {agency} en {localidad}. Si más adelante necesitaran orientación sobre "
            "la vivienda familiar —valoración, trámites o una posible venta—, estaré encantado de "
            "ayudarles, sin ninguna prisa ni compromiso, cuando ustedes lo consideren oportuno.\n\n"
            "Un respetuoso saludo,\n{agent} — {agency} — {phone}"
        ),
        "asunto": "",
        "notas": (
            "MUY SENSIBLE. No llamar. Enviar SOLO pasado un tiempo prudencial (mínimo 30-60 días). "
            "Mejor aún: espera a que aparezca el edicto de herederos (señal de que tramitan la "
            "sucesión) y usa entonces el guion de herencia. «{familia}» = familiares de la esquela."
        ),
    },
}

# Customer-facing fields the LLM may polish (never the internal canal/notas).
_POLISH_FIELDS = ("guion_llamada", "mensaje", "asunto")


# ── Local placeholder fill ───────────────────────────────────────────────────


class _SafeDict(dict):
    """format_map helper: leave unknown {placeholders} intact instead of crashing."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _fmt_price(price: object) -> str:
    try:
        return f"{int(price):,} €".replace(",", ".")
    except (TypeError, ValueError):
        return "el precio indicado"


def _fmt_familia(lead: dict) -> str:
    names = []
    raw = lead.get("heir_names_json")
    if raw:
        try:
            names = [n for n in json.loads(raw) if n]
        except (json.JSONDecodeError, TypeError):
            names = []
    if not names and lead.get("heir_name"):
        names = [lead["heir_name"]]
    return ", ".join(names) if names else "los familiares"


def _fill_values(lead: dict) -> dict:
    from nadia_ai.config import AGENT_AGENCY, AGENT_NAME, AGENT_PHONE

    return {
        "agent": AGENT_NAME,
        "agency": AGENT_AGENCY,
        "phone": AGENT_PHONE,
        "causante": (lead.get("causante") or "").strip() or "la persona fallecida",
        "localidad": (lead.get("localidad") or lead.get("region") or "").strip() or "su localidad",
        "barrio": (lead.get("address") or lead.get("localidad") or "").strip() or "su zona",
        "precio": _fmt_price(lead.get("price_eur")),
        "notaria": (lead.get("juzgado") or "").strip() or "la notaría/juzgado correspondiente",
        "procedimiento": (lead.get("procedimiento") or "").strip() or "(ref. en el edicto)",
        "familia": _fmt_familia(lead),
    }


# ── Optional, privacy-safe LLM polish (cached per lead-type) ─────────────────

_POLISH_CACHE: dict[str, dict] = {}


def _chat(prompt: str, timeout: int = 60) -> str | None:
    """One OpenAI-compatible chat call. Returns content text, or None on failure."""
    from nadia_ai.config import OUTREACH_API_KEY, OUTREACH_API_URL, OUTREACH_MODEL

    if not OUTREACH_API_KEY:
        return None
    try:
        resp = requests.post(
            OUTREACH_API_URL,
            headers={"Authorization": f"Bearer {OUTREACH_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": OUTREACH_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.4,
                "max_tokens": 1200,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"].get("content") or ""
    except Exception as e:  # noqa: BLE001 — any failure → deterministic template
        logger.info("Outreach LLM polish unavailable (%s) — using template.", e)
        return None


def _parse_json(content: str) -> dict | None:
    cleaned = re.sub(r"```(?:json)?", "", content or "").strip()
    for candidate in (cleaned, (re.search(r"\{.*\}", cleaned, re.DOTALL) or [None])[0]):
        if not candidate:
            continue
        try:
            out = json.loads(candidate if isinstance(candidate, str) else candidate.group(0))
            if isinstance(out, dict):
                return out
        except (json.JSONDecodeError, AttributeError):
            continue
    return None


def _polish(ltype: str) -> dict:
    """LLM-polished template for a type (placeholders preserved), cached. Falls back
    to the deterministic template — and validates the LLM kept the {placeholders}."""
    if ltype in _POLISH_CACHE:
        return _POLISH_CACHE[ltype]

    base = TEMPLATES[ltype]
    fields = {k: base[k] for k in _POLISH_FIELDS if base.get(k)}
    result = dict(base)

    from nadia_ai.config import AGENT_AGENCY

    prompt = (
        "Eres copywriter experto en captación inmobiliaria en España. Mejora estos textos de "
        f"contacto para un agente de {AGENT_AGENCY}: más naturales, cálidos y persuasivos, pero "
        "respetuosos y SIN presión (cumpliendo RGPD). REGLA CRÍTICA: conserva EXACTAMENTE los "
        "marcadores entre llaves (p. ej. {causante}, {barrio}, {precio}, {agent}, {agency}, "
        "{phone}, {localidad}); NO los rellenes, traduzcas ni elimines. Responde SOLO un JSON con "
        "las mismas claves de entrada.\n\nTextos:\n" + json.dumps(fields, ensure_ascii=False)
    )
    content = _chat(prompt)
    parsed = _parse_json(content) if content else None
    if parsed:
        for k in fields:
            new = parsed.get(k)
            # Accept only if a non-empty string that preserved the {agent} anchor.
            if isinstance(new, str) and new.strip() and "{agent}" in new:
                result[k] = new.strip()
        logger.info("Outreach template polished by LLM for type=%s", ltype)

    _POLISH_CACHE[ltype] = result
    return result


# ── Public API ───────────────────────────────────────────────────────────────


def render_outreach(lead: dict, use_llm: bool = False) -> dict:
    """Render ready-to-send outreach for one lead.

    Returns a dict: tipo, canal, guion_llamada, mensaje, asunto, notas, lead_ref.
    Empty string for fields that don't apply to the channel (e.g. no call script
    for a letter). Deterministic unless use_llm=True (and a key is configured)."""
    ltype = lead_type(lead)
    tpl = _polish(ltype) if use_llm else TEMPLATES[ltype]
    vals = _SafeDict(_fill_values(lead))

    def fill(text: str) -> str:
        return text.format_map(vals) if text else ""

    return {
        "lead_ref": lead.get("id") or lead.get("listing_id") or "",
        "tipo": TIPO_LABEL[ltype],
        "canal": tpl["canal"],
        "guion_llamada": fill(tpl.get("guion_llamada", "")),
        "mensaje": fill(tpl.get("mensaje", "")),
        "asunto": fill(tpl.get("asunto", "")),
        "notas": fill(tpl.get("notas", "")),
    }
