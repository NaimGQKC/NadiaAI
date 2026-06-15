"""Scraper for FSBO ("venta por particular") listings from pisos.com.

For a listing agent the highest-ROI new lead is a *for-sale-by-owner* flat: an
owner already trying to sell, with no agent in between, who is exactly the person
to pitch a mandate to. pisos.com exposes a private-seller filter
(``/de_particulares/``) that returns only owner-posted ads, served as clean HTML
with no anti-bot — unlike idealista/milanuncios (DataDome 403).

This source is SELF-CONTAINED: it does not feed the edict/heredero pipeline
(no death/inheritance signal), so it returns plain dicts (not EdictRecord) and is
exported on its own via ``tools/fsbo_export.py`` — never merged into nadia_ai.db.

Card structure (``.ad-preview``) as observed on pisos.com 2026-06:
    a.ad-preview__title   -> title text + relative detail URL (also yields the id)
    .ad-preview__price    -> "222.500 €"   (euro sign is mojibake at source)
    .ad-preview__subtitle -> "Delicias (Zaragoza Capital)"  (barrio + provincia)
    .ad-preview__char     -> ["4 habs.", "1 baño", "106 m²"]
The card has no phone, but the *detail page* exposes the owner's direct number for
~60% of particular ads (the rest route through pisos.com's switchboard). A second
pass (``enrich_phones=True``) fetches each detail page and fills that number — the
single thing that turns this from a property list into a call list.
"""

import logging
import re
import time
from datetime import date

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("nadia_ai.scrapers.fsbo")

BASE = "https://www.pisos.com"
# Private-seller ("de particulares") sale search, by localidad slug.
SEARCH = BASE + "/venta/pisos-{loc}/de_particulares/"
MAX_PAGES = 5          # politeness cap on pagination per localidad
REQUEST_TIMEOUT = 20   # seconds
SLEEP_BETWEEN = 0.5    # polite delay between page fetches

SESSION = requests.Session()
SESSION.headers.update({
    # pisos.com needs a real browser UA + Spanish locale; a bot UA gets thinner HTML.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
})

# "222.500 €" / "222.500" -> 222500   (drop thousands dots + any currency junk)
_PRICE_RE = re.compile(r"\d[\d.]*")
_INT_RE = re.compile(r"\d+")
# A detail URL looks like /comprar/piso-zaragoza_capital_centro50008-35890816334_100500/
# The long numeric id segment is what we dedup on.
_ID_RE = re.compile(r"-(\d{6,})_")
# Spanish mobile/landline, best-effort (rarely present in card HTML).
_PHONE_RE = re.compile(r"\b([6789]\d{2}[\s.]?\d{3}[\s.]?\d{3})\b")
# pisos.com's own switchboard — appears on ads where the owner did NOT publish a
# direct number. It is NOT the seller, so exclude it from phone enrichment.
_PISOS_SWITCHBOARDS = {"976200648"}
# On the detail page the owner's phone sits in a telefono/phone/whatsapp context
# (e.g. '"telefono":"876754067"'). 9 digits, Spanish mobile/landline (starts 6-9).
_DETAIL_PHONE_RE = re.compile(
    r"(?:tel[eé]fono|telephone|\"phone\"|whatsapp)[\"'>:\s]{1,4}([6-9]\d{8})",
    re.IGNORECASE,
)


def _to_int(text: str | None, pattern: re.Pattern = _INT_RE) -> int | None:
    """First integer in *text* (dots stripped first), else None."""
    if not text:
        return None
    m = pattern.search(text.replace(".", ""))
    return int(m.group()) if m else None


def _parse_price(text: str | None) -> int | None:
    """'222.500 €' -> 222500. Ignores 'A consultar' and other non-numeric prices."""
    if not text:
        return None
    m = _PRICE_RE.search(text)
    return int(m.group().replace(".", "")) if m else None


def _listing_id(href: str) -> str | None:
    """Stable id from a detail URL; falls back to the path slug."""
    m = _ID_RE.search(href)
    if m:
        return m.group(1)
    slug = href.strip("/").rsplit("/", 1)[-1]
    return slug or None


def _split_location(subtitle: str) -> tuple[str | None, str | None]:
    """'Delicias (Zaragoza Capital)' -> ('Delicias', 'Zaragoza Capital').

    The barrio/address is the part before the parenthesis; the provincia/city is
    inside it. Either side may be missing on sparse cards.
    """
    if not subtitle:
        return None, None
    m = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", subtitle)
    if m:
        addr = m.group(1).strip() or None
        prov = m.group(2).strip() or None
        return addr, prov
    return subtitle.strip() or None, None


def _parse_card(card, localidad: str, scraped_at: str) -> dict | None:
    """Turn one ``.ad-preview`` card into a lead dict, or None if unusable."""
    link = card.select_one("a.ad-preview__title") or card.select_one(".ad-preview__title")
    href = (link.get("href") if link else None) or card.get("data-lnk-href")
    if not href:
        return None
    listing_url = href if href.startswith("http") else BASE + href
    listing_id = _listing_id(href)

    title = link.get_text(strip=True) if link else None

    price_el = card.select_one(".ad-preview__price")
    price_eur = _parse_price(price_el.get_text(strip=True) if price_el else None)

    # Characteristics: rooms / bathrooms / m² — order is not guaranteed, so match
    # on the unit text rather than position.
    rooms = m2 = None
    for ch in card.select(".ad-preview__char"):
        txt = ch.get_text(" ", strip=True)
        low = txt.lower()
        if "hab" in low and rooms is None:
            rooms = _to_int(txt)
        elif ("m²" in txt or "m2" in low) and "construido" not in low and m2 is None:
            m2 = _to_int(txt)

    sub = card.select_one(".ad-preview__subtitle")
    address, provincia = _split_location(sub.get_text(" ", strip=True) if sub else "")

    # Phone is essentially always behind a click; grab it only if it happens to be
    # in the card markup. Avoid matching the m²/id digits we already parsed.
    phone = None
    pm = _PHONE_RE.search(card.get_text(" ", strip=True))
    if pm:
        phone = pm.group(1)

    return {
        "source": "fsbo_pisos",
        "listing_id": listing_id,
        "title": title,
        "price_eur": price_eur,
        "m2": m2,
        "rooms": rooms,
        "localidad": localidad,
        "provincia": provincia,
        "address": address,
        "listing_url": listing_url,
        "phone": phone,
        "anunciante": "particular",
        "scraped_at": scraped_at,
    }


def _parse_page(html: str, localidad: str, scraped_at: str) -> list[dict]:
    """Parse all FSBO cards on one search-results page."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for card in soup.select(".ad-preview"):
        lead = _parse_card(card, localidad, scraped_at)
        if lead and lead.get("listing_url"):
            out.append(lead)
    return out


def _page_url(localidad: str, page: int) -> str:
    """Page 1 is the bare /de_particulares/ URL; later pages append /<n>/."""
    base = SEARCH.format(loc=localidad)
    return base if page == 1 else f"{base}{page}/"


def _normalize_phone(num: str) -> str:
    """Group a 9-digit Spanish number as 'NNN NNN NNN' (else return as-is)."""
    n = re.sub(r"\D", "", num)
    return f"{n[:3]} {n[3:6]} {n[6:]}" if len(n) == 9 else num


def extract_detail_phone(html: str) -> str | None:
    """Return the owner's phone from a detail-page HTML, excluding pisos.com's
    switchboard. Pure function (no network) so it unit-tests off a fixture."""
    for m in _DETAIL_PHONE_RE.finditer(html or ""):
        num = m.group(1)
        if num not in _PISOS_SWITCHBOARDS:
            return _normalize_phone(num)
    return None


def _fetch_detail_phone(url: str) -> str | None:
    """Fetch a listing detail page and extract the owner's direct phone, or None.
    Best-effort: any network/parse failure returns None (phone stays unset)."""
    try:
        resp = SESSION.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.debug("FSBO detail fetch failed (%s): %s", url, e)
        return None
    return extract_detail_phone(resp.text)


def scrape_fsbo(
    localidades: list[str] | None = None,
    max_per_loc: int = 60,
    enrich_phones: bool = True,
    phone_lookup_cap: int = 80,
) -> list[dict]:
    """Scrape FSBO (private-seller) sale listings from pisos.com.

    Args:
        localidades: pisos.com URL slugs (e.g. "zaragoza_capital"). Defaults to
            ["zaragoza_capital"].
        max_per_loc: hard cap on listings returned per localidad.
        enrich_phones: if True, fetch each listing's detail page to fill the owner's
            direct phone (~60% hit rate). Set False for a fast metadata-only pull.
        phone_lookup_cap: max detail pages to fetch for phones (politeness cap).

    Returns:
        Deduplicated list of lead dicts (see module docstring for the schema).
        Degrades gracefully: on any request failure it logs a warning and returns
        whatever was gathered so far.
    """
    if localidades is None:
        localidades = ["zaragoza_capital"]
    scraped_at = date.today().isoformat()

    results: list[dict] = []
    seen: set[str] = set()
    for loc in localidades:
        loc_count = 0
        for page in range(1, MAX_PAGES + 1):
            if loc_count >= max_per_loc:
                break
            url = _page_url(loc, page)
            try:
                resp = SESSION.get(url, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
            except requests.RequestException as e:
                logger.warning("pisos.com FSBO fetch failed (%s): %s", url, e)
                break  # stop this localidad, keep whatever we have

            page_leads = _parse_page(resp.text, loc, scraped_at)
            if not page_leads:
                # No cards => past the last page (or layout changed); move on.
                logger.info("pisos.com FSBO: no cards on %s, stopping localidad", url)
                break

            new_on_page = 0
            for lead in page_leads:
                if loc_count >= max_per_loc:
                    break
                key = lead.get("listing_id") or lead["listing_url"]
                if key in seen:
                    continue
                seen.add(key)
                results.append(lead)
                loc_count += 1
                new_on_page += 1

            logger.info(
                "pisos.com FSBO %s p%d: %d cards, %d new (loc total %d)",
                loc, page, len(page_leads), new_on_page, loc_count,
            )
            # If a whole page was duplicates, we've looped past fresh inventory.
            if new_on_page == 0:
                break
            time.sleep(SLEEP_BETWEEN)

    # Second pass: fill the owner's direct phone from each detail page. The card
    # never carries it, but ~60% of particular detail pages do — this is what makes
    # the feed a call list rather than a property list. Polite + capped; each
    # listing degrades silently to phone=None.
    if enrich_phones:
        targets = [r for r in results if not r.get("phone")][:phone_lookup_cap]
        filled = 0
        for i, lead in enumerate(targets):
            phone = _fetch_detail_phone(lead["listing_url"])
            if phone:
                lead["phone"] = phone
                filled += 1
            if i < len(targets) - 1:
                time.sleep(SLEEP_BETWEEN)
        logger.info(
            "pisos.com FSBO phone enrichment: %d/%d listings got a direct owner phone",
            filled, len(targets),
        )

    logger.info("pisos.com FSBO scrape complete: %d listings", len(results))
    return results
