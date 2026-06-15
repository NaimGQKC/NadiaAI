"""Scraper for Heraldo de Aragón esquelas — the Zaragoza newspaper obituaries.

Unlike Memora/Defunciones (name + date only), a Heraldo esquela is the classic
Spanish newspaper death notice that NAMES THE FAMILY:

    "Carmen Alejandre Sabio. Falleció en Zaragoza el día 13 de junio de 2026.
     Sus apenados: esposo, don José Javier Gil Barranco; hijas, Virginia y
     Ángela; nietos, ...; hermana, Olga Paula; ..."

That makes it the highest-value Zaragoza-specific source: it yields both the
causante and the heirs (spouse + children), so these leads are actionable, not
just a death signal.

The "Sus apenados:" family block is parsed DETERMINISTICALLY here
(`parse_family_heirs`), the same way `utils/edict_parse.py` reads templated BOE
fields with regex instead of spending LLM tokens: the block is a fixed,
semicolon-delimited list of relations, so a regex reads the spouse/children/
siblings exactly and for free, with no hallucination risk. The flaky paid LLM
step stays only as a fallback for pages that lack the block. This source must NOT
be in the obituary-skip list in utils/extraction.py.
"""

import hashlib
import json
import logging
import re
import sqlite3
import time
from datetime import UTC, datetime, timedelta

import requests

from nadia_ai.models import EdictRecord
from nadia_ai.utils.names import (
    clean_name_list,
    is_valid_person_name,
    strip_honorific,
)

logger = logging.getLogger("nadia_ai.scrapers.heraldo")

BASE = "https://www.heraldo.es"
LISTING = f"{BASE}/esquelas/"
MAX_DETAILS = 60  # politeness cap per run
# The bare /esquelas/ listing returns only the ~10 most-recent notices (mixed
# provinces). The search form's `province` filter returns each Aragón province's
# own recent esquelas — measured higher (Zaragoza alone: 14 vs 10) and, more
# importantly, it guarantees Aragón coverage that the mixed default listing can
# silently drop. We sweep all three and dedup by slug.
ARAGON_PROVINCES = ("zaragoza", "huesca", "teruel")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "NadiaAI/1.0 (real-estate lead pipeline; polite scraper)",
    "Accept": "text/html",
    "Accept-Language": "es-ES,es;q=0.9",
})

_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

# /esquelas/<slug>.html detail links
_DETAIL_RE = re.compile(r'href="(/esquelas/[a-z0-9][a-z0-9\-]+\.html)"')
# "Falleció en Zaragoza el día 13 de junio de 2026"  /  "el 13 de junio de 2026"
_FALLECIO_RE = re.compile(
    r"Falleci[óo]\s+en\s+([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ\.\-/ ]+?)[,\.]?\s+"
    r"(?:el\s+(?:d[íi]a\s+)?)?(\d{1,2})\s+de\s+([a-záéíóú]+)\s+de\s+(\d{4})",
    re.IGNORECASE,
)

# ── "Sus apenados:" family block ────────────────────────────────────────────
# The block lists the bereaved relatives, grouped by relation and separated by
# ";": "Sus apenados: esposo, don José Javier Gil Barranco; hijas, Virginia y
# Ángela; nietos, ...; hermana, Olga Paula; ...". Each relation segment leads
# with a kinship label ("esposo", "hijas", "hermanos"…) followed by the names.

# The JSON-LD "description" carries the block as raw JSON (escaped "\r\n",
# unicode \uXXXX). Pull that string first; the visible text is the fallback.
_JSONLD_DESC_RE = re.compile(r'"description"\s*:\s*"((?:[^"\\]|\\.)*)"')
# Start of the family block (accent/case-insensitive). The deceased's own
# relatives are introduced by "Sus apenados"/"Sus afligidos"; "Su <relation>" is
# the singular variant some notices use ("Su esposa e hijos:").
_APENADOS_RE = re.compile(r"(?i)sus?\s+(?:apenad|afligid|desconsolad)\w*\s*:?\s*")
# Sentinels that close the block — funeral/liturgy boilerplate or end-of-string.
_BLOCK_END_RE = re.compile(
    r"(?i)\b(?:D\.?\s*E\.?\s*P\.?|Q\.?\s*E\.?\s*P\.?\s*D\.?|R\.?\s*I\.?\s*P\.?|"
    r"Descanse|Descansa|Funeral|El\s+funeral|Misa|La\s+misa|Capilla|"
    r"Tanatorio|Cementerio|Habiendo\s+recibido|Rogad|Se\s+ruega|"
    r"La\s+familia\s+agradece|Agradecen)\b"
)
# The kinship label that prefixes each relation segment, e.g. "esposo," / "hijas,".
# Used to drop the label and detect spouse/children for ordering.
_RELATION_LABEL_RE = re.compile(
    r"(?i)^\s*(?:y\s+)?(?:su\s+|sus\s+)?"
    r"(esposo|esposa|viudo|viuda|hijo|hija|hijos|hijas|nieto|nieta|nietos|nietas|"
    r"hermano|hermana|hermanos|hermanas|padre|madre|padres|sobrino|sobrina|"
    r"sobrinos|sobrinas|primo|prima|primos|primas|t[ií]o|t[ií]a|t[ií]os|t[ií]as|"
    r"abuelo|abuela|abuelos|nuera|yerno|cu[ñn]ado|cu[ñn]ada|cu[ñn]ados|cu[ñn]adas|"
    r"ahijado|ahijada|consuegr\w+|familia(?:res)?|dem[áa]s\s+familia\w*|"
    r"pariente\w*)\b\s*[,:]?\s*"
)
# Relations whose members are the spouse/children — the primary sellers — and so
# are ordered first in the returned list.
_PRIMARY_RELATIONS = {
    "esposo", "esposa", "viudo", "viuda",
    "hijo", "hija", "hijos", "hijas",
}
# Collective references that close an esquela ("y demás familia", "sus parientes",
# "toda la familia"). They survive name validation as two-token phrases but name
# no individual, so they're dropped before validation. Matched accent/case-
# insensitively against the lowercased, accent-stripped candidate.
_COLLECTIVE_RE = re.compile(
    r"(?i)\b(?:demas|toda|sus|los|las|amig\w+|allegad\w+|parient\w+|"
    r"familiar\w*)\b.*\bfamilia\b|\bfamilia\b|\bparient\w+\b|\ballegad\w+\b|"
    r"\bamig\w+\b|\bvecin\w+\b"
)


def _is_collective(candidate: str) -> bool:
    """True for collective family references ("demás familia", "parientes") that
    name no individual and so are never heirs."""
    from nadia_ai.utils.names import _strip_accents  # local: shared normaliser

    return bool(_COLLECTIVE_RE.search(_strip_accents(candidate).lower()))


def _unescape_jsonld(raw: str) -> str:
    """Decode a raw JSON-LD string value (escaped \\r\\n, \\uXXXX, \\") to text.

    The "description" field is matched out of the page before the JSON is parsed,
    so it still carries JSON escapes; re-quoting and parsing turns "\\u00e1" → "á"
    and "\\r\\n" → newlines. `strict=False` tolerates literal control characters
    inside the value (some pages embed a real newline rather than "\\n"). On any
    failure, fall back to decoding only the JSON escape sequences by hand — never
    `unicode_escape`, which corrupts the already-UTF-8 accented bytes."""
    try:
        return json.loads(f'"{raw}"', strict=False)
    except (json.JSONDecodeError, ValueError):
        out = re.sub(
            r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), raw
        )
        for esc, ch in (("\\n", "\n"), ("\\r", "\r"), ("\\t", "\t"),
                        ("\\/", "/"), ('\\"', '"'), ("\\\\", "\\")):
            out = out.replace(esc, ch)
        return out


def _block_text(html_or_text: str) -> str:
    """Return the searchable text that should contain the family block.

    Prefers the JSON-LD "description" (cleanest, no surrounding markup); falls
    back to the HTML stripped of tags. Accepts plain text unchanged."""
    m = _JSONLD_DESC_RE.search(html_or_text)
    if m:
        desc = _unescape_jsonld(m.group(1))
        if "apenad" in desc.lower() or "afligid" in desc.lower() or "su " in desc.lower():
            return desc
    # Fall back to visible text: drop tags, collapse whitespace.
    text = re.sub(r"<[^>]+>", " ", html_or_text)
    return re.sub(r"[ \t\r\n ]+", " ", text)


def parse_family_heirs(html_or_text: str) -> list[str]:
    """Extract the bereaved family (heirs) from an esquela's "Sus apenados:" block.

    Pure function: deterministic regex only, no network or LLM. Mirrors
    `utils.edict_parse` — read the templated block exactly rather than spend
    extraction tokens on it. The block names the spouse, children and other
    relatives, who are the heirs/sellers of the estate.

    Parsing: locate "Sus apenados:" (JSON-LD description first, visible text as
    fallback), cut it at a funeral/liturgy sentinel ("D.E.P", "Funeral",
    "Descanse"…) or end-of-string, split into relation segments by ";", strip the
    leading kinship label, then split names by "," and " y ". Each candidate is
    run through `strip_honorific` → `is_valid_person_name` → `clean_name_list`.

    Note on first-name-only members ("hijas, Virginia y Ángela"): the mandatory
    project validator requires a first name + at least one surname, so bare given
    names are dropped here. That is intentional — full-name relatives are the
    searchable, actionable sellers; admitting surname-less tokens would only
    pollute the contact/dedup fields. Spouse/children relations are ordered first,
    the rest follow; order is otherwise preserved and duplicates removed.

    Returns [] when there is no block or no validated name in it.
    """
    if not html_or_text:
        return []

    text = _block_text(html_or_text)
    m = _APENADOS_RE.search(text)
    if not m:
        return []

    block = text[m.end():]
    end = _BLOCK_END_RE.search(block)
    if end:
        block = block[: end.start()]
    block = block.strip()
    if not block:
        return []

    primary: list[str] = []
    secondary: list[str] = []
    for segment in block.split(";"):
        segment = segment.strip()
        if not segment:
            continue
        label_m = _RELATION_LABEL_RE.match(segment)
        relation = ""
        if label_m:
            relation = label_m.group(1).lower()
            segment = segment[label_m.end():]
        # Names within a relation are separated by "," and the connector " y "/" e ".
        candidates = re.split(r"\s*,\s*|\s+[ye]\s+", segment)
        bucket = primary if relation in _PRIMARY_RELATIONS else secondary
        for cand in candidates:
            cand = strip_honorific(cand.strip(" .;,"))
            if _is_collective(cand):
                continue
            if is_valid_person_name(cand):
                bucket.append(cand)

    # clean_name_list re-validates and dedupes accent-insensitively, preserving
    # order; concatenating primary first keeps spouse/children at the front while
    # a name repeated across buckets is emitted once (at its first position).
    return clean_name_list(primary + secondary)


def _parse_detail(html: str, slug: str) -> dict | None:
    """Pull deceased name, death city, and death date from an esquela page."""
    # Deceased name: the <title> is "<Name> | Esquela Heraldo de Aragón".
    tm = re.search(r"<title>\s*(.*?)\s*[|<]", html, re.IGNORECASE | re.DOTALL)
    name = (tm.group(1).strip() if tm else "") or slug.replace("-", " ").title()
    name = re.sub(r"\s+", " ", name)
    # Esquela headlines lead with a courtesy title ("Doña Carmen Alejandre
    # Sabio") that pollutes the causante field used for dedup/contact search.
    name = strip_honorific(name)
    if not is_valid_person_name(name):
        return None

    # Visible text for the death-location/date line.
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    localidad, dod = None, None
    fm = _FALLECIO_RE.search(text)
    if fm:
        localidad = fm.group(1).strip().title()
        month = _MONTHS.get(fm.group(3).lower())
        if month:
            try:
                dod = datetime(int(fm.group(4)), month, int(fm.group(2)), tzinfo=UTC)
            except ValueError:
                dod = None
    # Heirs come straight from the "Sus apenados:" family block — parsed here
    # deterministically rather than left to the LLM step.
    heirs = parse_family_heirs(html)
    return {"name": name, "localidad": localidad, "dod": dod, "heirs": heirs}


def _detail_links(html: str) -> list[str]:
    seen, out = set(), []
    for path in _DETAIL_RE.findall(html):
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def scrape_heraldo(since: datetime | None = None) -> list[EdictRecord]:
    """Scrape Heraldo de Aragón esquelas published recently.

    Returns EdictRecord objects; heir names are filled later by LLM extraction.
    """
    if since is None:
        since = datetime.now(UTC) - timedelta(days=90)

    # Sweep the bare listing plus each Aragón province filter, deduping by slug.
    # If every request fails we abort; partial failures just yield fewer links.
    links: list[str] = []
    seen_links: set[str] = set()
    fetched_any = False
    for params in ({}, *({"province": p} for p in ARAGON_PROVINCES)):
        try:
            resp = SESSION.get(LISTING, params=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning("Heraldo listing fetch failed (%s): %s", params or "default", e)
            continue
        fetched_any = True
        for path in _detail_links(resp.text):
            if path not in seen_links:
                seen_links.add(path)
                links.append(path)
    if not fetched_any:
        logger.error("Heraldo listing unreachable for all province filters")
        return []

    links = links[:MAX_DETAILS]
    records: list[EdictRecord] = []
    for path in links:
        slug = path.rsplit("/", 1)[-1].removesuffix(".html")
        try:
            d = SESSION.get(BASE + path, timeout=20)
            if d.status_code != 200:
                continue
        except requests.RequestException as e:
            logger.warning("Heraldo detail fetch failed for %s: %s", slug, e)
            continue

        parsed = _parse_detail(d.text, slug)
        if not parsed:
            continue
        dod = parsed["dod"]
        if dod and dod < since:
            continue

        records.append(EdictRecord(
            source="heraldo",
            source_id=hashlib.md5(slug.encode("utf-8")).hexdigest()[:12],
            edict_type="esquela_prensa",
            published_at=dod,
            source_url=BASE + path,
            causante=parsed["name"],
            localidad=parsed["localidad"] or "Zaragoza",
            fecha_fallecimiento=dod.strftime("%Y-%m-%d") if dod else None,
            heir_names=parsed["heirs"],
        ))
        time.sleep(0.3)

    logger.info("Heraldo de Aragón esquelas: %d records", len(records))
    return records


# ── Backfill ────────────────────────────────────────────────────────────────


def backfill_heraldo_heirs(conn: sqlite3.Connection, limit: int | None = None) -> dict:
    """Re-fetch Heraldo esquela pages and fill heir names from the "Sus apenados:"
    family block for leads that landed before deterministic parsing existed (or
    whose flaky LLM pass left them empty).

    Mirrors `utils.edict_parse.backfill_edict_contacts`: idempotent and additive —
    only writes `heir_names_json`/`heir_name` when currently empty (COALESCE /
    NULLIF semantics), so it can run every pipeline pass without clobbering richer
    data. Commits per row. Network-bound (one page fetch per lead), so `limit`
    caps a pass. Returns and logs a stats dict.
    """
    conn.row_factory = sqlite3.Row
    # Heraldo leads that still have no heirs recorded. Match by source label or
    # subsource so it works regardless of which the merge wrote.
    q = """
        SELECT id, source_urls, heir_names_json, heir_name
        FROM leads
        WHERE (sources LIKE '%Heraldo%' OR subsource = 'Heraldo')
          AND (heir_names_json IS NULL OR heir_names_json IN ('', '[]'))
        ORDER BY first_seen_at DESC
    """
    if limit:
        q += f" LIMIT {int(limit)}"
    leads = conn.execute(q).fetchall()

    stats = {"scanned": 0, "heirs_added": 0}
    for lead in leads:
        urls = json.loads(lead["source_urls"] or "[]")
        url = next((u for u in urls if "heraldo.es" in u), None)
        if not url:
            continue
        try:
            resp = SESSION.get(url, timeout=20)
            if resp.status_code != 200:
                continue
        except requests.RequestException as e:
            logger.warning("Heraldo backfill fetch failed for %s: %s", url, e)
            continue
        stats["scanned"] += 1

        heirs = parse_family_heirs(resp.text)
        if not heirs:
            continue

        conn.execute(
            """
            UPDATE leads SET
                heir_names_json = COALESCE(NULLIF(NULLIF(heir_names_json, ''), '[]'), ?),
                heir_name = COALESCE(NULLIF(heir_name, ''), ?),
                last_updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (json.dumps(heirs), heirs[0], lead["id"]),
        )
        conn.commit()
        stats["heirs_added"] += 1
        time.sleep(0.3)

    logger.info(
        "Heraldo heir backfill: scanned %d, heirs+ %d",
        stats["scanned"], stats["heirs_added"],
    )
    return stats
