"""Scraper for Servihabitat (CaixaBank bank-owned properties) in Zaragoza.

Servihabitat is CaixaBank's real-estate arm, selling repossessed properties.
Bank-owned properties are often priced below market and represent actionable
leads for a real-estate agent — either as direct purchases for clients or
as comparables for pricing nearby listings.

Flow:
1. Fetch the Zaragoza sitemap XML to discover property URLs
2. Parse each property page for price, address, m2, type, rooms
3. Return EdictRecord objects for the merge pipeline

These are NOT inheritance edicts — they're bank-owned property listings.
Leads from this source are Tier B (address + price, no causante).
"""

import hashlib
import logging
import re
import time

import requests

from nadia_ai.models import EdictRecord

logger = logging.getLogger("nadia_ai.scrapers.servihabitat")

SITEMAP_URL = "https://www.servihabitat.com/es/sitemap-es-zaragoza.xml"
MAX_PROPERTIES = 200  # Safety limit on individual property fetches

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "NadiaAI/1.0 (real-estate lead pipeline; polite scraper)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
    "Accept-Language": "es-ES,es;q=0.9",
})

# ---------------------------------------------------------------------------
# Sitemap parsing
# ---------------------------------------------------------------------------

# Match <loc> entries that look like property detail URLs
# Pattern: /es/venta/{type}/{location}/{numeric-id}
SITEMAP_LOC_RE = re.compile(r"<loc>\s*(https://www\.servihabitat\.com/es/venta/[^<]+?/(\d+))\s*</loc>")

# ---------------------------------------------------------------------------
# Property page extraction (regex-based, no BeautifulSoup)
# ---------------------------------------------------------------------------

# Price: look for euro amounts like "85.000 €" or "85,000 €" or structured data
PRICE_RE = re.compile(
    r'(?:class="[^"]*price[^"]*"[^>]*>|"price"\s*:\s*"?)'
    r"\s*([0-9.,]+)\s*[€]",
    re.IGNORECASE,
)
# Fallback: any prominent euro figure on the page
PRICE_FALLBACK_RE = re.compile(r"(\d{2,3}(?:[.,]\d{3})*)\s*€")

# Address text: prefer JSON-LD streetAddress, fallback to HTML class
ADDRESS_RE = re.compile(
    r'"streetAddress"\s*:\s*"([^"]+)"',
    re.IGNORECASE,
)
ADDRESS_FALLBACK_RE = re.compile(
    r'(?:class="[^"]*(?:address|location|direction)[^"]*"[^>]*>)\s*([^<]+)',
    re.IGNORECASE,
)

# Square meters
M2_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*m[²2\u00B2]", re.IGNORECASE)

# Rooms / habitaciones — match "3 hab." or "3 habitaciones" but NOT "200 habitantes"
ROOMS_RE = re.compile(r"(\d{1,2})\s*(?:hab\.(?!\w)|habitacion(?:es)?(?!\w)|dormitorio)", re.IGNORECASE)

# Property type from URL path segment: /es/venta/piso/..., /es/venta/casa/...
URL_TYPE_RE = re.compile(r"/es/venta/([^/]+)/([^/]+)/(\d+)")

# Title tag as fallback for address info
TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE)

# og:title meta tag — often has the best structured info
OG_TITLE_RE = re.compile(
    r'<meta\s+(?:property|name)="og:title"\s+content="([^"]+)"',
    re.IGNORECASE,
)


def _fetch_sitemap() -> list[tuple[str, str]]:
    """Fetch and parse the Zaragoza sitemap XML.

    Returns list of (full_url, numeric_id) tuples for property pages.
    """
    try:
        resp = SESSION.get(SITEMAP_URL, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Servihabitat sitemap fetch failed: %s", e)
        return []

    matches = SITEMAP_LOC_RE.findall(resp.text)
    logger.info("Servihabitat sitemap: %d property URLs found", len(matches))
    return matches  # list of (url, numeric_id)


def _extract_price(html: str) -> str | None:
    """Extract the property price from page HTML."""
    m = PRICE_RE.search(html)
    if m:
        return m.group(1).strip()
    m = PRICE_FALLBACK_RE.search(html)
    if m:
        return m.group(1).strip()
    return None


def _extract_address(html: str) -> str | None:
    """Extract address text from page HTML."""
    # Prefer JSON-LD streetAddress
    m = ADDRESS_RE.search(html)
    if m:
        return m.group(1).strip()
    # Fallback: HTML class
    m = ADDRESS_FALLBACK_RE.search(html)
    if m:
        text = m.group(1).strip()
        if text and len(text) > 3:
            return text
    # Last resort: <title>
    m = TITLE_RE.search(html)
    if m:
        title = m.group(1).strip()
        title = re.sub(r"\s*[|–—-]\s*Servihabitat.*$", "", title, flags=re.IGNORECASE)
        return title if title else None
    return None


def _extract_m2(html: str) -> str | None:
    """Extract square meters from page HTML."""
    m = M2_RE.search(html)
    return m.group(1).strip() if m else None


def _extract_rooms(html: str) -> str | None:
    """Extract number of rooms from page HTML."""
    m = ROOMS_RE.search(html)
    return m.group(1).strip() if m else None


def _parse_url_segments(url: str) -> tuple[str, str, str]:
    """Extract property type, location, and numeric ID from URL.

    Returns (prop_type, location, numeric_id). E.g.:
    ("piso", "zaragoza", "12345")
    """
    m = URL_TYPE_RE.search(url)
    if m:
        return m.group(1), m.group(2), m.group(3)
    return "", "", ""


def _make_source_id(numeric_id: str) -> str:
    """Generate a stable source_id from the Servihabitat property numeric ID."""
    return hashlib.md5(f"servihabitat:{numeric_id}".encode()).hexdigest()[:12]


def _fetch_property(url: str) -> str | None:
    """Fetch a single property page. Returns HTML or None on failure."""
    try:
        resp = SESSION.get(url, timeout=30)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        logger.warning("Servihabitat property fetch failed (%s): %s", url, e)
        return None


# ---------------------------------------------------------------------------
# Main scraper
# ---------------------------------------------------------------------------


def scrape_servihabitat() -> list[EdictRecord]:
    """Scrape Servihabitat Zaragoza property listings.

    Fetches the Zaragoza sitemap, then visits each property page to extract
    price, address, m2, type, and rooms. Returns EdictRecord objects.
    """
    url_pairs = _fetch_sitemap()
    if not url_pairs:
        logger.warning("Servihabitat: no property URLs from sitemap")
        return []

    # Apply safety limit
    if len(url_pairs) > MAX_PROPERTIES:
        logger.info(
            "Servihabitat: capping at %d properties (found %d)",
            MAX_PROPERTIES, len(url_pairs),
        )
        url_pairs = url_pairs[:MAX_PROPERTIES]

    all_records: list[EdictRecord] = []
    seen_ids: set[str] = set()

    for idx, (url, numeric_id) in enumerate(url_pairs):
        if numeric_id in seen_ids:
            continue
        seen_ids.add(numeric_id)

        prop_type, location, _ = _parse_url_segments(url)
        localidad = location.replace("-", " ").title() if location else "Zaragoza"

        html = _fetch_property(url)
        if not html:
            continue

        price = _extract_price(html)
        address = _extract_address(html)
        m2 = _extract_m2(html)
        rooms = _extract_rooms(html)

        # Build composite address field: "€85,000 — CL DELICIAS 15"
        address_parts = []
        if price:
            address_parts.append(f"€{price}")
        if address:
            address_parts.append(address)
        if m2:
            address_parts.append(f"{m2} m²")
        if rooms:
            address_parts.append(f"{rooms} hab")
        if prop_type:
            address_parts.append(prop_type)

        composite_address = " — ".join(address_parts) if address_parts else None

        source_id = _make_source_id(numeric_id)
        record = EdictRecord(
            source="servihabitat",
            source_id=source_id,
            edict_type="banco_inmueble",
            source_url=url,
            address=composite_address,
            localidad=localidad,
        )
        all_records.append(record)
        logger.info(
            "Servihabitat [%d/%d]: %s (id=%s, %s)",
            idx + 1, len(url_pairs), composite_address or "no data", source_id, url,
        )

        # Polite delay between property page fetches
        if idx < len(url_pairs) - 1:
            time.sleep(1.0)

    logger.info("Servihabitat scrape complete: %d records", len(all_records))
    return all_records
