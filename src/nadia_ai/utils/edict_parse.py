"""Deterministic field extraction from BOE edict text.

Why deterministic (not the LLM): BOE judicial (TEJU) and notarial (BOE-N) edicts
are templated — the justice system and the Notariado emit fixed labelled fields
(`ÓRGANO JUDICIAL`, `Teléfono:`, `PROCEDIMIENTO Número:`, `NOTARÍA DE …`). A regex
reads them exactly and for free, with no hallucination risk — strictly better than
spending extraction tokens here. The LLM stays for the *prose* an edict body can
contain (heir relationships, free-text domiciles); this module handles the
structured shell around it.

What it unlocks (the actionability gap these fields close):
  * TEJU judicial edicts named a court + phone + email + procedure number in every
    record, but the pipeline captured none of it (the field label is "Tribunal de
    Instancia", never the literal word "Juzgado", so the old `juzgado` scrape was
    0% on TEJU). That's the reachable handling office for 400+ herencia-yacente
    leads.
  * TEJU `DESTINATARIOS` blocks sometimes name the actual interested parties /
    heirs (with a partial NIF) alongside the "herencia yacente de <causante>"
    label — real named heirs the LLM heir-extraction was missing.
  * BOE-N notarial edicts occasionally landed with an empty `causante`; the name is
    always in the "Declaración de herederos ab intestato de <name>" title.

The text passed in is whatever `scrapers.boe.extract_pdf_text` returns (clean
UTF-8). All parsers are pure functions of that string so they unit-test without a
network or DB.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3

from nadia_ai.utils.names import clean_name_list, is_valid_person_name

logger = logging.getLogger("nadia_ai.edict_parse")


def _collapse(text: str | None) -> str:
    """Flatten the PDF's hard-wrapped lines into one searchable string."""
    return " ".join((text or "").split())


def _days_from_plazo(plazo: str) -> int | None:
    """'veinte días' / '5 días' → integer days, for the edict-window clock.

    The publication→claim deadline varies by edict (5, 20, 30 days…); the merge
    clock uses a flat 30d proxy, but when the text states the real term we can do
    better. Returns None if unparseable."""
    if not plazo:
        return None
    plazo = plazo.lower()
    m = re.search(r"(\d+)", plazo)
    if m:
        return int(m.group(1))
    words = {
        "cinco": 5, "diez": 10, "quince": 15, "veinte": 20,
        "treinta": 30, "dos meses": 60, "un mes": 30,
    }
    for w, n in words.items():
        if w in plazo:
            return n
    return None


# ── BOE-TEJU (judicial edictos) ────────────────────────────────────────────

# The TEJU template puts the handling office in a labelled header. The field is
# "Sección Civil … del Tribunal de Instancia de <city>", never "Juzgado", which is
# why the original `juzgado`-keyed scrape captured 0% of it.
_TEJU_COURT = re.compile(r"(?i)RGANO JUDICIAL\s+(.+?)\s+(?:Plaza\s+n|Direcci)")
_TEJU_PHONE = re.compile(r"(?i)Tel[eé]fono:?\s*([0-9]{6,9})")
_TEJU_EMAIL = re.compile(r"(?i)Correo\s+electr[oó]nico:?\s*([^\s]+@[^\s]+)")
_TEJU_PROC = re.compile(r"(?i)PROCEDIMIENTO.*?N[uú]mero:?\s*([0-9]{1,7}/[0-9]{4})")
_TEJU_PLAZO = re.compile(r"(?i)Plazo:?\s*([a-zñáéíóú0-9 ]+?d[ií]as)")
_TEJU_NIF = re.compile(r"-\s*NIF/NIE")
_CAP_TOKEN = re.compile(r"[A-ZÑÁÉÍÓÚ][A-Za-zñÑáéíóúÁÉÍÓÚ.'-]*")
_HY_DE = re.compile(
    r"(?i)herencia\s+yacente\s+(?:y\s+herederos\s+desconocidos\s+)?de\s+"
    r"([A-Za-zñÑáéíóúÁÉÍÓÚ .'-]{4,55}?)(?:\s+Se\s+hace\s+saber|\s*-\s*NIF|$)"
)
# Connector/boilerplate tokens that are never part of a person's name.
_NAME_STOP = {"HERENCIA", "YACENTE", "HEREDEROS", "DESCONOCIDOS", "DE", "Y", "DESTINATARIOS"}


def _extract_destinatarios(block: str) -> tuple[list[str], str]:
    """Split a TEJU DESTINATARIOS block into (named living parties, causante).

    Each addressee carries its own "- NIF/NIE". The hard case is that names run
    together with no delimiter and a "HERENCIA YACENTE DE <causante>" label can sit
    between two real parties. Heuristic: for each NIF marker, look at the capitalised
    tokens immediately before it. If "HERENCIA"/"YACENTE" is within that short
    window the addressee is the estate itself (→ causante); otherwise the last few
    tokens are a living party (→ heir). Capping at 4 trailing tokens keeps an
    adjacent label/causante name from bleeding into the party.
    """
    parties: list[str] = []
    causante = ""
    for m in _TEJU_NIF.finditer(block):
        words = _CAP_TOKEN.findall(block[: m.start()])
        if not words:
            continue
        window = words[-6:]
        if any(w.upper() in ("HERENCIA", "YACENTE") for w in window):
            # Estate addressee: the causante's name follows the YACENTE/DE marker.
            tail = [w for w in window if w.upper() not in _NAME_STOP]
            cand = " ".join(tail[-4:]).title()
            if not causante and is_valid_person_name(cand):
                causante = cand
        else:
            cand = " ".join(words[-4:]).title()
            if is_valid_person_name(cand):
                parties.append(cand)
    return clean_name_list(parties), causante


def parse_teju_edict(text: str) -> dict:
    """Extract the handling court, its contact, the procedure number and any named
    interested parties from a BOE judicial edict.

    Returns a dict with keys: court, phone, email, procedure, plazo_days,
    parties (named heirs/interested parties — may be empty), causante (parsed from
    the 'herencia yacente de …' label, for backfilling an empty DB value). Missing
    fields are '' or [] / None so callers can COALESCE without clobbering.
    """
    t = _collapse(text)
    out: dict = {
        "court": "", "phone": "", "email": "", "procedure": "",
        "plazo_days": None, "parties": [], "causante": "",
    }
    if not t:
        return out

    m = _TEJU_COURT.search(t)
    if m:
        out["court"] = m.group(1).strip(" .")
    m = _TEJU_PHONE.search(t)
    if m:
        out["phone"] = m.group(1)
    m = _TEJU_EMAIL.search(t)
    if m:
        out["email"] = m.group(1).rstrip(". ")
    m = _TEJU_PROC.search(t)
    if m:
        out["procedure"] = m.group(1)
    m = _TEJU_PLAZO.search(t)
    if m:
        out["plazo_days"] = _days_from_plazo(m.group(1))

    # DESTINATARIOS block: between the header and "Se hace saber …".
    i = t.find("DESTINATARIOS")
    block = ""
    if i >= 0:
        j = t.find("Se hace saber", i)
        block = t[i + len("DESTINATARIOS") : j if j > 0 else i + 400]

    out["parties"], out["causante"] = _extract_destinatarios(block)
    if not out["causante"]:
        m = _HY_DE.search(block or t)
        if m and is_valid_person_name(m.group(1).strip().title()):
            out["causante"] = m.group(1).strip().title()
    return out


# ── BOE-N (notarial declaración de herederos) ──────────────────────────────

_NOT_NOTARY = re.compile(r"(?i)NOTAR[IÍ]A\s+DE\s+(.+?)\s+(?:DE\s+[A-ZÑÁÉÍÓÚ].+?\.\s|\.\s*Anuncio)")
_NOT_CAUSANTE = re.compile(
    r"(?i)declaraci[oó]n\s+de\s+herederos\s+ab\s*intestato\s+de\s+"
    r"(?:don |do[ñn]a |d\.\s*|d[ªº]\.\s*)?([A-Za-zñÑáéíóúÁÉÍÓÚ .'-]{5,60}?)[,.]"
)
_NOT_HEIR = re.compile(
    r"(?i)por\s+su\s+\w+\s+(?:don |do[ñn]a |d\.\s*|d[ªº]\.\s*)([A-Za-zñÑáéíóúÁÉÍÓÚ .'-]{5,60}?)\s+se\s+"
)
_NOT_PROTO = re.compile(r"(?i)n[uú]mero\s+([0-9]{1,6})\s+de\s+mi\s+protocolo")
# Último domicilio of the causante (often the inherited property — the postal
# target). The OLD pattern allowed only letters, so it stopped at the first digit
# and dropped the house NUMBER — making every address unresolvable in Catastro,
# which requires a number. We now capture the full street + number (+ city).
# Markers are tried most-precise first so "vecino de Huesca con domicilio en
# C/ Mayor 23" yields the street, not "Huesca".
_DOMICILE_MARKERS = (
    r"[uú]ltimo\s+domicilio(?:\s+conocido)?\s+en",
    r"con\s+domicilio\s+en",
    r"domiciliad[oa]\s+en",
    r"vecin[oa]\s+de",
)
# Where the address ends: sentence punctuation, a protocol/number reference, or a
# boilerplate continuation ("a fin de que…", "de mi protocolo").
_DOMICILE_STOP = re.compile(
    r"(?i)(?:[.;\n]|,\s*n[uú]mero\b|,\s*n[.º°]|\s+de\s+mi\s+protocolo\b|"
    r"\s+a\s+fin\b|\s+al\s+objeto\b|\s+para\s+que\b|\s+y\s+a\s+los\b|\s+donde\b)"
)


def _extract_domicile(t: str) -> str:
    """Pull the street-level último domicilio from edict text, keeping the number."""
    for marker in _DOMICILE_MARKERS:
        m = re.search(
            r"(?i)" + marker + r"\s+([A-Za-zñÑáéíóúÁÉÍÓÚ0-9ºª°/.\-,\s]{4,80})", t
        )
        if not m:
            continue
        chunk = m.group(1)
        stop = _DOMICILE_STOP.search(chunk)
        if stop:
            chunk = chunk[: stop.start()]
        chunk = " ".join(chunk.split()).strip(" ,.-")
        if chunk:
            return chunk
    return ""
_NOT_OFFICE = re.compile(r"(?i)despacho\s+profesional\s+sito\s+en\s+(.+?)[,.](?:\s+a\s+fin|\s+de\s+[A-Z])")


def parse_notaria_edict(text: str) -> dict:
    """Extract notary, causante, the requesting heir, último domicilio, protocol and
    the notary's office from a BOE-N notarial edict. All fields '' when absent."""
    t = _collapse(text)
    out = {"notary": "", "causante": "", "heir": "", "domicile": "", "protocol": "", "office": ""}
    if not t:
        return out
    m = _NOT_NOTARY.search(t)
    if m:
        out["notary"] = m.group(1).strip(" .")
    m = _NOT_CAUSANTE.search(t)
    if m and is_valid_person_name(m.group(1).strip().title()):
        out["causante"] = m.group(1).strip().title()
    m = _NOT_HEIR.search(t)
    if m and is_valid_person_name(m.group(1).strip().title()):
        out["heir"] = m.group(1).strip().title()
    out["domicile"] = _extract_domicile(t)
    m = _NOT_PROTO.search(t)
    if m:
        out["protocol"] = m.group(1)
    m = _NOT_OFFICE.search(t)
    if m:
        out["office"] = m.group(1).strip()
    return out


# ── Backfill ───────────────────────────────────────────────────────────────


def backfill_edict_contacts(conn: sqlite3.Connection, limit: int | None = None) -> dict:
    """Re-read the source PDFs of judicial/notarial leads and fill the structured
    contact fields the LLM pass leaves blank: the handling office (court/notary), its
    phone/email, the procedure number, and any named interested parties.

    Idempotent and additive — only writes a field that is currently empty (COALESCE
    semantics), so it can run every pipeline pass and never clobbers richer data.
    Returns counts. Network-bound (one PDF fetch per lead), so `limit` caps a pass.
    """
    from nadia_ai.scrapers.boe import extract_pdf_text

    conn.row_factory = sqlite3.Row
    # Leads whose office contact hasn't been resolved yet. `contact_source` marks a
    # lead as office-enriched so re-runs skip it.
    q = """
        SELECT id, subsource, causante, heir_name, heir_names_json, source_urls
        FROM leads
        WHERE subsource IN ('BOE-TEJU', 'BOE-N', 'BOE-V.B')
          AND (contact_source IS NULL OR contact_source NOT IN ('juzgado', 'notaria'))
          AND source_urls LIKE '%boe.es%'
        ORDER BY first_seen_at DESC
    """
    if limit:
        q += f" LIMIT {int(limit)}"
    leads = conn.execute(q).fetchall()

    stats = {"scanned": 0, "office": 0, "phone": 0, "heirs_added": 0, "causante_filled": 0, "address": 0}
    for lead in leads:
        urls = json.loads(lead["source_urls"] or "[]")
        url = next((u for u in urls if "boe.es" in u), None)
        if not url:
            continue
        text = extract_pdf_text(url)
        if not text:
            continue
        stats["scanned"] += 1

        is_notarial = lead["subsource"] == "BOE-N"
        parsed = parse_notaria_edict(text) if is_notarial else parse_teju_edict(text)
        office = parsed.get("notary") if is_notarial else parsed.get("court")
        source = "notaria" if is_notarial else "juzgado"
        phone = "" if is_notarial else parsed.get("phone", "")
        email = "" if is_notarial else parsed.get("email", "")
        procedure = parsed.get("protocol", "") if is_notarial else parsed.get("procedure", "")

        # Último domicilio of the causante → the lead's street address (the postal
        # target / Catastro key). Only persist a STREET-level value (contains a
        # number); a city-only string can't be geocoded and just shadows localidad.
        domicile = parsed.get("domicile", "") if is_notarial else ""
        address_fill = domicile.strip() if re.search(r"\d", domicile or "") else ""

        # Named parties/heir: TEJU `parties`, BOE-N single `heir`.
        new_heirs = parsed.get("parties", []) if not is_notarial else (
            [parsed["heir"]] if parsed.get("heir") else []
        )
        existing_heirs = json.loads(lead["heir_names_json"] or "[]")
        merged_heirs = clean_name_list(existing_heirs + new_heirs)
        heir_added = len(merged_heirs) > len(existing_heirs)

        causante_fill = parsed.get("causante", "") if not (lead["causante"] or "").strip() else ""

        if not office and not phone and not heir_added and not causante_fill and not address_fill:
            # Nothing parseable — still mark scanned so we don't refetch forever.
            conn.execute(
                "UPDATE leads SET contact_source = COALESCE(NULLIF(contact_source,''), ?) WHERE id = ?",
                (source, lead["id"]),
            )
            conn.commit()
            continue

        conn.execute(
            """
            UPDATE leads SET
                juzgado = COALESCE(NULLIF(juzgado, ''), ?),
                contact_phone = COALESCE(NULLIF(contact_phone, ''), ?),
                contact_email = COALESCE(NULLIF(contact_email, ''), ?),
                contact_source = ?,
                contact_confidence = COALESCE(NULLIF(contact_confidence, ''), 'oficina'),
                contact_source_url = COALESCE(NULLIF(contact_source_url, ''), ?),
                procedimiento = COALESCE(NULLIF(procedimiento, ''), ?),
                causante = COALESCE(NULLIF(causante, ''), ?),
                direccion = COALESCE(NULLIF(direccion, ''), ?),
                heir_names_json = ?,
                heir_name = COALESCE(NULLIF(heir_name, ''), ?),
                last_updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                office or "",
                phone or "",
                email or "",
                source,
                url,
                procedure or "",
                causante_fill or "",
                address_fill or "",
                json.dumps(merged_heirs),
                merged_heirs[0] if merged_heirs else "",
                lead["id"],
            ),
        )
        conn.commit()
        if office:
            stats["office"] += 1
        if phone:
            stats["phone"] += 1
        if address_fill:
            stats["address"] += 1
        if heir_added:
            stats["heirs_added"] += 1
        if causante_fill:
            stats["causante_filled"] += 1

    logger.info(
        "Edict-contact backfill: scanned %d, office %d, phone %d, heirs+ %d, causante+ %d",
        stats["scanned"], stats["office"], stats["phone"], stats["heirs_added"], stats["causante_filled"],
    )
    return stats
