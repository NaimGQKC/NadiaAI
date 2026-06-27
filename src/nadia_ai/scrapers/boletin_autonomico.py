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

HEADERS = {
    "User-Agent": "NadiaAI/0.1 (lead-generation research)",
    "Accept": "text/html,application/xhtml+xml",
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


def scrape_boletin(cfg: dict, since: datetime | None = None) -> list[EdictRecord]:
    """Run the generic bulletin scrape for one comunidad config. Never raises."""
    records: list[EdictRecord] = []
    seen: set[str] = set()
    source = cfg["source"]
    try:
        for kw in _QUERIES:
            url = cfg["search_url"].format(q=quote(kw))
            html = _get(url)
            if not html:
                continue
            links = _extract_edict_links(html, cfg["base"])
            logger.info("%s '%s': %d candidate edicts", source, kw, len(links))
            for link_url, title in links:
                sid = hashlib.md5(f"{source}:{link_url}".encode()).hexdigest()[:12]
                if sid in seen:
                    continue
                seen.add(sid)
                records.append(
                    EdictRecord(
                        source=source,
                        source_id=sid,
                        edict_type="declaracion_herederos_abintestato",
                        published_at=datetime.now(UTC),
                        source_url=link_url,
                        causante=_causante_from_title(title),
                    )
                )
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
    "search_url": "https://www.bocm.es/buscador-boletin?palabra={q}",
}

DOGC_CONFIG = {
    "source": "dogc",  # Diari Oficial de la Generalitat de Catalunya
    "base": "https://dogc.gencat.cat",
    "search_url": "https://dogc.gencat.cat/es/cercador-dogc/?accio=cerca&text={q}",
}


def scrape_bocm(since: datetime | None = None) -> list[EdictRecord]:
    """Madrid — Boletín Oficial de la Comunidad de Madrid (inheritance edicts)."""
    return scrape_boletin(BOCM_CONFIG, since)


def scrape_dogc(since: datetime | None = None) -> list[EdictRecord]:
    """Cataluña — Diari Oficial de la Generalitat de Catalunya (inheritance edicts)."""
    return scrape_boletin(DOGC_CONFIG, since)
