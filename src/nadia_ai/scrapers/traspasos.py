"""Scraper for Traspasos Aragón (generational business transfers).

The Cámaras de Aragón operate traspasosaragon.com as the official platform
for generational business transfers in the region. Business transfers
(traspasos) represent commercial real-estate opportunities — the physical
premises of these businesses are often included in or adjacent to the deal.

Strategy:
  1. Paginate through /traspasos/page/N/ (WordPress standard pagination)
  2. Parse each listing card for business name, sector, address, price
  3. Optionally fetch detail pages for richer data (capped for politeness)
  4. Filter to Zaragoza province only (configurable)
  5. Return EdictRecord objects

These are NOT inheritance edicts — they're business transfer listings.
Leads from this source are Tier B (business name + address, commercial context).
"""

import hashlib
import logging
import re
import time

import requests
from bs4 import BeautifulSoup

from nadia_ai.models import EdictRecord

logger = logging.getLogger("nadia_ai.scrapers.traspasos")

BASE_URL = "https://www.traspasosaragon.com/traspasos/"
PAGE_URL_TEMPLATE = "https://www.traspasosaragon.com/traspasos/page/{page}/"
MAX_PAGES = 10  # Safety limit (currently ~6 pages)
MAX_DETAIL_FETCHES = 30  # Cap on individual detail page fetches

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "NadiaAI/1.0 (real-estate lead pipeline; polite scraper)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
    "Accept-Language": "es-ES,es;q=0.9",
})

# Province filter options
ZARAGOZA_KEYWORDS = {"zaragoza", "zgz", "zar"}

# Sector emoji markers used in the site's listing cards
SECTOR_MAP = {
    "comercio": "Comercio",
    "hostelería": "Hostelería",
    "hosteleria": "Hostelería",
    "industria": "Industria",
    "servicios": "Servicios",
    "otros": "Otros",
}

# Price extraction patterns
PRICE_RE = re.compile(
    r"(?:precio|price)\s*:?\s*(?:€\s*)?([0-9.,]+)\s*(?:€)?",
    re.IGNORECASE,
)
PRICE_INLINE_RE = re.compile(r"(\d{1,3}(?:[.,]\d{3})*)\s*€")


def _make_source_id(url_slug: str) -> str:
    """Generate a stable source_id from the listing URL slug."""
    return hashlib.md5(f"traspaso:{url_slug}".encode()).hexdigest()[:12]


def _extract_sector(text: str) -> str:
    """Extract the business sector from a listing card's text."""
    text_lower = text.lower()
    for keyword, label in SECTOR_MAP.items():
        if keyword in text_lower:
            return label
    return ""


def _extract_price(text: str) -> str | None:
    """Extract the asking price from listing text."""
    m = PRICE_RE.search(text)
    if m:
        return m.group(1).strip()
    m = PRICE_INLINE_RE.search(text)
    if m:
        return m.group(1).strip()
    if "consultar" in text.lower() or "negociable" in text.lower():
        return "A consultar"
    return None


def _extract_address_from_marker(text: str) -> str | None:
    """Extract address from the 📍 emoji marker in listing text."""
    # The site uses 📍 followed by the address
    idx = text.find("📍")
    if idx == -1:
        return None
    # Take text after the marker up to the next emoji or section break
    after = text[idx + len("📍"):].strip()
    # Cut at next emoji marker or newline
    for marker in ["📋", "\n", "Precio", "Se traspasa", "Ver detalles"]:
        cut_idx = after.find(marker)
        if cut_idx > 0:
            after = after[:cut_idx]
    return after.strip() if after.strip() else None


def _extract_province(text: str) -> str:
    """Extract province from listing text (Zaragoza, Huesca, or Teruel)."""
    text_lower = text.lower()
    if "zaragoza" in text_lower:
        return "Zaragoza"
    if "huesca" in text_lower:
        return "Huesca"
    if "teruel" in text_lower:
        return "Teruel"
    return "Aragón"


def _is_zaragoza(text: str) -> bool:
    """Check if a listing is in Zaragoza province."""
    return "zaragoza" in text.lower()


def _parse_listing_page(html: str, zaragoza_only: bool = True) -> list[dict]:
    """Parse a listings page and return raw listing dicts."""
    soup = BeautifulSoup(html, "html.parser")
    listings: list[dict] = []

    # WordPress listing cards — look for links to /traspaso/ detail pages
    # Each card is typically wrapped in an <a> tag or <article>
    for link in soup.find_all("a", href=re.compile(r"/traspaso/[^/]+/")):
        href = link.get("href", "")
        if not href or "/traspasos/" in href:
            continue

        card_text = link.get_text(separator="\n", strip=True)
        if len(card_text) < 10:
            continue

        # Extract URL slug for dedup
        slug_match = re.search(r"/traspaso/([^/]+)/", href)
        slug = slug_match.group(1) if slug_match else hashlib.md5(
            href.encode()
        ).hexdigest()[:12]

        # Extract province and filter
        province = _extract_province(card_text)
        if zaragoza_only and province != "Zaragoza":
            continue

        # Extract business name — first substantial line of text
        lines = [l.strip() for l in card_text.split("\n") if l.strip()]
        business_name = ""
        for line in lines:
            # Skip emoji-only or very short lines
            clean = re.sub(r"[📋📍🔆⚙️🏭]", "", line).strip()
            if len(clean) >= 5 and "ver detalles" not in clean.lower():
                business_name = clean
                break

        address = _extract_address_from_marker(card_text)
        sector = _extract_sector(card_text)
        price = _extract_price(card_text)

        listings.append({
            "slug": slug,
            "url": href if href.startswith("http") else f"https://www.traspasosaragon.com{href}",
            "business_name": business_name,
            "address": address,
            "sector": sector,
            "price": price,
            "province": province,
            "raw_text": card_text[:500],
        })

    # Deduplicate by slug within the page
    seen_slugs: set[str] = set()
    unique: list[dict] = []
    for listing in listings:
        if listing["slug"] not in seen_slugs:
            seen_slugs.add(listing["slug"])
            unique.append(listing)

    return unique


def _has_next_page(html: str) -> bool:
    """Check if there's a 'Siguiente' (Next) pagination link."""
    soup = BeautifulSoup(html, "html.parser")
    next_link = soup.find("a", string=re.compile(r"Siguiente|›|»|Next", re.IGNORECASE))
    return next_link is not None


def _fetch_detail(url: str) -> dict:
    """Fetch a detail page for richer data. Returns extra fields dict."""
    try:
        resp = SESSION.get(url, timeout=20)
        resp.raise_for_status()
        resp.encoding = "utf-8"
    except requests.RequestException as e:
        logger.debug("Traspasos detail fetch failed (%s): %s", url, e)
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text(separator=" ", strip=True)

    extra: dict = {}
    # Try to find a more complete address from the detail page
    for el in soup.find_all(["p", "div", "span"]):
        el_text = el.get_text(strip=True)
        if "📍" in el_text or "dirección" in el_text.lower():
            addr = _extract_address_from_marker(el_text)
            if addr and len(addr) > len(extra.get("address", "")):
                extra["address"] = addr

    # Extract a description for context
    content = soup.find("div", class_=re.compile(r"entry-content|post-content|content"))
    if content:
        desc = content.get_text(separator=" ", strip=True)[:300]
        extra["description"] = desc

    return extra


def scrape_traspasos(
    zaragoza_only: bool = True,
    fetch_details: bool = False,
) -> list[EdictRecord]:
    """Scrape Traspasos Aragón business transfer listings.

    Args:
        zaragoza_only: If True, only return listings in Zaragoza province.
        fetch_details: If True, fetch detail pages for richer data
                       (capped at MAX_DETAIL_FETCHES; prioritizes newest).

    Returns:
        List of EdictRecord objects with source="traspasos".
    """
    all_listings: list[dict] = []
    seen_slugs: set[str] = set()
    page = 1

    while page <= MAX_PAGES:
        url = BASE_URL if page == 1 else PAGE_URL_TEMPLATE.format(page=page)

        try:
            logger.info("Traspasos page %d: %s", page, url)
            resp = SESSION.get(url, timeout=30)
            if resp.status_code == 404:
                logger.info("Traspasos: page %d returned 404, stopping", page)
                break
            resp.raise_for_status()
            resp.encoding = "utf-8"
        except requests.RequestException as e:
            logger.error("Traspasos page %d failed: %s", page, e)
            break

        listings = _parse_listing_page(resp.text, zaragoza_only=zaragoza_only)
        new_on_page = 0
        for listing in listings:
            if listing["slug"] not in seen_slugs:
                seen_slugs.add(listing["slug"])
                all_listings.append(listing)
                new_on_page += 1

        logger.info("Traspasos page %d: %d new listings", page, new_on_page)

        if not _has_next_page(resp.text):
            break

        page += 1
        time.sleep(1.0)  # Polite delay

    # Optionally fetch detail pages (prioritize newest = first in list)
    if fetch_details and all_listings:
        detail_count = min(len(all_listings), MAX_DETAIL_FETCHES)
        logger.info("Traspasos: fetching %d detail pages", detail_count)
        for i in range(detail_count):
            listing = all_listings[i]
            extra = _fetch_detail(listing["url"])
            if extra.get("address"):
                listing["address"] = extra["address"]
            if extra.get("description"):
                listing["description"] = extra["description"]
            time.sleep(1.0)  # Polite delay

    # Convert to EdictRecords
    records: list[EdictRecord] = []
    for listing in all_listings:
        # Build composite address: "€Price — Address — Sector"
        addr_parts = []
        if listing.get("price"):
            price_val = listing["price"]
            if price_val != "A consultar":
                addr_parts.append(f"€{price_val}")
            else:
                addr_parts.append("Precio: A consultar")
        if listing.get("address"):
            addr_parts.append(listing["address"])
        if listing.get("sector"):
            addr_parts.append(listing["sector"])
        composite_address = " — ".join(addr_parts) if addr_parts else listing.get("raw_text", "")[:120]

        source_id = _make_source_id(listing["slug"])
        record = EdictRecord(
            source="traspasos",
            source_id=source_id,
            edict_type="traspaso_negocio",
            source_url=listing["url"],
            causante=listing.get("business_name") or None,
            address=composite_address,
            localidad=listing.get("province") or "Zaragoza",
        )
        records.append(record)
        logger.info(
            "Traspaso: %s | %s | %s",
            listing.get("business_name", "-")[:40],
            listing.get("sector", "-"),
            listing.get("province", "-"),
        )

    logger.info(
        "Traspasos scrape complete: %d records (zaragoza_only=%s)",
        len(records), zaragoza_only,
    )
    return records
