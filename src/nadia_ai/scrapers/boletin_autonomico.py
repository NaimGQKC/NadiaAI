"""Generic scraper for autonomous-community official bulletins (BOCM, DOGC, …).

Why a generic engine: each comunidad has its own boletín with a different website,
but they all publish the same kind of edict we want — judicial/notarial inheritance
notices ("herencia yacente", "declaración de herederos", "abintestato") that often
carry the estate's address. Rather than hand-write a bespoke scraper per region, this
engine takes a small config (base URL + a search-URL template + the source tag) and:

  1. queries the bulletin's search for each inheritance keyword,
  2. finds the result links whose anchor text looks like an inheritance edict,
  3. emits an EdictRecord per link with the source_url.

The heavy extraction (causante, heirs, street address, RC) is then done by the
EXISTING pipeline — `run_heir_extraction` (LLM) fetches each source_url, and
`backfill_edict_contacts` runs the deterministic finca/domicilio parser. So adding a
region is just adding a config here; the extraction quality is shared with BOE/BOA.

IMPORTANT (operational): the `search_url` templates below are best-effort and must be
validated against the live site on the self-hosted runner (the cloud IP can't reach
them). The engine is fully defensive — any network/parse error logs and returns [],
so a wrong endpoint yields zero leads but never breaks the pipeline.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

from nadia_ai.models import EdictRecord
from nadia_ai.utils.names import is_valid_person_name

logger = logging.getLogger("nadia_ai.scrapers.boletin")

# Government bulletin sites often block obvious bot User-Agents, so present a
# realistic browser UA to maximise the chance of a 200 on the live runner.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
TIMEOUT = (8, 25)

# Anchor text that marks an inheritance edict worth following.
_INHERITANCE_RE = re.compile(
    r"(?i)(herencia\s+yacente|declaraci[oó]n\s+de\s+herederos|abintestato|"
    r"ab\s+intestato|herederos?\s+(?:desconocidos|abintestato)|sucesi[oó]n\s+(?:legal|intestada))"
)

# Search keywords run against each bulletin's search engine.
_QUERIES = (
    "herencia yacente",
    "declaración de herederos",
    "abintestato",
)

# Best-effort causante name from a result title ("...de D./Dña. NOMBRE..."). The
# keyword/honorific parts are case-insensitive (scoped flags) but the NAME anchor
# stays case-sensitive — a global (?i) would let the uppercase anchor grab the
# lowercase "abintestato" itself.
_CAUSANTE_RE = re.compile(
    r"(?i:herencia\s+yacente|declaraci[oó]n\s+de\s+herederos|abintestato)\s+"
    r"(?i:de\s+)?(?i:D\.?[aª]?\s+|D[ñÑ]a\.?\s+|do[nñ]a?\s+)?"
    r"([A-ZÁÉÍÓÚÑ][a-zñáéíóúA-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-zñáéíóúA-ZÁÉÍÓÚÑ]+){1,4})"
)


def _causante_from_title(title: str) -> str | None:
    m = _CAUSANTE_RE.search(title or "")
    if not m:
        return None
    name = m.group(1).strip().title()
    return name if is_valid_person_name(name) else None


def _get(url: str) -> str | None:
    try:
        resp = SESSION.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        logger.info("Boletín fetch failed %s: %s", url[:80], e)
        return None


def _extract_edict_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """Find (url, anchor-text) pairs whose text looks like an inheritance edict.

    Generic across bulletin search pages: we don't depend on a specific HTML
    layout, only on the link text containing an inheritance marker."""
    out: list[tuple[str, str]] = []
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return out
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        if text and _INHERITANCE_RE.search(text):
            out.append((urljoin(base_url, a["href"]), text))
    return out


def _record(source: str, link_url: str, title: str, seen: set[str]) -> EdictRecord | None:
    sid = hashlib.md5(f"{source}:{link_url}".encode()).hexdigest()[:12]
    if sid in seen:
        return None
    seen.add(sid)
    return EdictRecord(
        source=source,
        source_id=sid,
        edict_type="declaracion_herederos_abintestato",
        published_at=datetime.now(UTC),
        source_url=link_url,
        causante=_causante_from_title(title),
    )


def _scrape_search(cfg: dict, seen: set[str]) -> list[EdictRecord]:
    """Keyword-search mode: query the bulletin's buscador (and any fixed seed pages,
    e.g. a "Administración de Justicia" section index), follow the edict links."""
    records: list[EdictRecord] = []
    source = cfg["source"]
    # Fixed seed pages (section indexes) — a confirmed discovery surface that doesn't
    # depend on guessing a search param.
    for seed in cfg.get("seed_urls", []):
        html = _get(seed)
        if not html:
            continue
        for link_url, title in _extract_edict_links(html, cfg["base"]):
            rec = _record(source, link_url, title, seen)
            if rec:
                records.append(rec)
    for kw in _QUERIES:
        html = _get(cfg["search_url"].format(q=quote(kw)))
        if not html:
            continue
        links = _extract_edict_links(html, cfg["base"])
        logger.info("%s '%s': %d candidate edicts", source, kw, len(links))
        for link_url, title in links:
            rec = _record(source, link_url, title, seen)
            if rec:
                records.append(rec)
    return records


def _extract_numbers(html: str, pattern: re.Pattern) -> list[int]:
    """Bulletin numbers captured by `pattern` (group 1), unique, most-recent first."""
    nums = {int(m) for m in pattern.findall(html or "") if m.isdigit()}
    return sorted(nums, reverse=True)


def _scrape_index_crawl(cfg: dict, seen: set[str]) -> list[EdictRecord]:
    """Justice-section mode (BOJA): year index → recent boletín numbers → each
    boletín's "Administración de justicia" section page → inheritance-edict links.

    Uses BOJA's CONFIRMED stable structure (…/boja/{year}/{num}/s4 = section 4) so
    we scan exactly the judicial section instead of guessing a search param."""
    records: list[EdictRecord] = []
    source = cfg["source"]
    year = datetime.now(UTC).year
    index_html = _get(cfg["index_url"].format(year=year))
    if not index_html:
        return records
    numbers = _extract_numbers(index_html, cfg["num_re"])
    logger.info("%s index: %d boletines found", source, len(numbers))
    for num in numbers[: cfg.get("max_boletines", 8)]:
        page = _get(cfg["section_url"].format(year=year, num=num))
        if not page:
            continue
        for link_url, title in _extract_edict_links(page, cfg["base"]):
            rec = _record(source, link_url, title, seen)
            if rec:
                records.append(rec)
    return records


def scrape_boletin(cfg: dict, since: datetime | None = None) -> list[EdictRecord]:
    """Run the generic bulletin scrape for one comunidad config. Never raises."""
    records: list[EdictRecord] = []
    seen: set[str] = set()
    source = cfg["source"]
    try:
        if cfg.get("mode") == "index_crawl":
            records = _scrape_index_crawl(cfg, seen)
        else:
            records = _scrape_search(cfg, seen)
    except Exception as e:  # defensive: a bulletin must never break the run
        logger.warning("%s scrape failed: %s", source, e)
    logger.info("%s scrape complete: %d records", source, len(records))
    return records


# ── Per-comunidad configs ────────────────────────────────────────────────────
# search_url: a keyword search returning an HTML results page with edict links.
# These endpoints are best-effort and MUST be validated on the self-hosted runner
# (adjust the template if the live site differs); the engine fails safe meanwhile.

BOCM_CONFIG = {
    "source": "bocm",  # Boletín Oficial de la Comunidad de Madrid
    "base": "https://www.bocm.es",
    # Confirmed (research): BOCM has a "Sección IV. Administración de Justicia" where
    # herencia-yacente / declaración-de-herederos edicts are published, as PDFs at
    # /boletin/CM_Orden_BOCM/{Y}/{M}/{D}/BOCM-{YYYYMMDD}-{n}.PDF. The section index
    # page is a stable discovery surface; the buscador is the keyword fallback.
    "seed_urls": ["https://www.bocm.es/seccion-iv-administracion-de-justicia"],
    "search_url": "https://www.bocm.es/buscador-boletin?palabra={q}",
}

DOGC_CONFIG = {
    "source": "dogc",  # Diari Oficial de la Generalitat de Catalunya
    "base": "https://dogc.gencat.cat",
    "search_url": "https://dogc.gencat.cat/es/cercador-dogc/?accio=cerca&text={q}",
}

# BOJA uses its CONFIRMED, stable structure (research, not a guessed search param):
# year index lists boletín numbers → each boletín's section 4 (Administración de
# justicia) at /boja/{year}/{num}/s4 → HTML edict links (/boja/{year}/{num}/{m}.html).
BOJA_CONFIG = {
    "source": "boja",  # Boletín Oficial de la Junta de Andalucía
    "base": "https://www.juntadeandalucia.es",
    "mode": "index_crawl",
    "index_url": "https://www.juntadeandalucia.es/eboja/{year}.html",
    "num_re": re.compile(r"/eboja/\d{4}/(\d+)/"),
    "section_url": "https://www.juntadeandalucia.es/boja/{year}/{num}/s4",
    "max_boletines": 8,
}

DOGV_CONFIG = {
    "source": "dogv",  # Diari Oficial de la Generalitat Valenciana
    "base": "https://dogv.gva.es",
    "search_url": "https://dogv.gva.es/es/resultats-dogv?text={q}",
}


def scrape_bocm(since: datetime | None = None) -> list[EdictRecord]:
    """Madrid — Boletín Oficial de la Comunidad de Madrid (inheritance edicts)."""
    return scrape_boletin(BOCM_CONFIG, since)


def scrape_dogc(since: datetime | None = None) -> list[EdictRecord]:
    """Cataluña — Diari Oficial de la Generalitat de Catalunya (inheritance edicts)."""
    return scrape_boletin(DOGC_CONFIG, since)


def scrape_boja(since: datetime | None = None) -> list[EdictRecord]:
    """Andalucía — Boletín Oficial de la Junta de Andalucía (inheritance edicts)."""
    return scrape_boletin(BOJA_CONFIG, since)


def scrape_dogv(since: datetime | None = None) -> list[EdictRecord]:
    """C. Valenciana — Diari Oficial de la Generalitat Valenciana (inheritance edicts)."""
    return scrape_boletin(DOGV_CONFIG, since)
