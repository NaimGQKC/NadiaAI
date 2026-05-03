"""Scraper for Zaragoza city vacant plots (solares) via open-data JSON API.

The Zaragoza municipal open data portal exposes a JSON endpoint with all
registered vacant plots (solares) in the city. Vacant plots are investment
opportunities — they can be developed, sold, or used as leverage for nearby
property valuations.

API: https://www.zaragoza.es/sede/servicio/solar.json
- ~3,100 total records, ~2,400 with status="Solar" (vacant private plots)
- Coordinates are UTM EPSG:25830 (zone 30N)
- No date field; we fetch all and rely on DB dedup

These are NOT inheritance edicts — they're municipal registry data.
Leads from this source are Tier B (address only, no causante).
"""

import hashlib
import logging
import math
import time

import requests

from nadia_ai.models import EdictRecord

logger = logging.getLogger("nadia_ai.scrapers.solares")

API_BASE = "https://www.zaragoza.es/sede/servicio/solar.json"
PAGE_SIZE = 50  # rows per request
MAX_PAGES = 60  # safety limit: 60 * 50 = 3000 records

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "NadiaAI/1.0 (real-estate lead pipeline; polite scraper)",
    "Accept": "application/json",
})


# ---------------------------------------------------------------------------
# UTM Zone 30N (EPSG:25830) → WGS84 conversion
# Pure-math implementation — no pyproj dependency needed.
# ---------------------------------------------------------------------------

def _utm_to_latlon(easting: float, northing: float) -> tuple[float, float]:
    """Convert UTM Zone 30N coordinates to WGS84 (lat, lon).

    Uses the Karney/Helmert series expansion for the transverse Mercator.
    Accurate to ~1 m which is more than sufficient for reverse geocoding.
    """
    # WGS84 ellipsoid constants
    a = 6378137.0  # semi-major axis
    f = 1 / 298.257223563  # flattening
    e2 = 2 * f - f * f  # eccentricity squared

    k0 = 0.9996  # UTM scale factor
    lon0 = math.radians(-3.0)  # central meridian for zone 30

    x = easting - 500000.0  # remove false easting
    y = northing  # northern hemisphere — no false northing

    # Footpoint latitude
    m = y / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))

    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
        + (151 * e1**3 / 96) * math.sin(6 * mu)
        + (1097 * e1**4 / 512) * math.sin(8 * mu)
    )

    n1 = a / math.sqrt(1 - e2 * math.sin(phi1) ** 2)
    t1 = math.tan(phi1) ** 2
    c1 = (e2 / (1 - e2)) * math.cos(phi1) ** 2
    r1 = a * (1 - e2) / (1 - e2 * math.sin(phi1) ** 2) ** 1.5
    d = x / (n1 * k0)

    lat = phi1 - (n1 * math.tan(phi1) / r1) * (
        d**2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * (e2 / (1 - e2))) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * (e2 / (1 - e2)) - 3 * c1**2)
        * d**6
        / 720
    )
    lon = (
        d
        - (1 + 2 * t1 + c1) * d**3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * (e2 / (1 - e2)) + 24 * t1**2)
        * d**5
        / 120
    ) / math.cos(phi1)

    return math.degrees(lat), math.degrees(lon) + math.degrees(lon0)


# ---------------------------------------------------------------------------
# Reverse geocoding via Nominatim (OpenStreetMap)
# ---------------------------------------------------------------------------

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"


def _reverse_geocode(lat: float, lon: float) -> str | None:
    """Get a street address from lat/lon using Nominatim.

    Returns a short address string or None on failure.
    Rate-limited: caller must throttle to 1 req/s.
    """
    try:
        resp = SESSION.get(
            _NOMINATIM_URL,
            params={
                "lat": f"{lat:.6f}",
                "lon": f"{lon:.6f}",
                "format": "json",
                "addressdetails": "1",
                "zoom": "18",
            },
            headers={"User-Agent": "NadiaAI/1.0 (lead pipeline; contact: dev@nadiaai.local)"},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        addr = data.get("address", {})
        road = addr.get("road", "")
        house = addr.get("house_number", "")
        suburb = addr.get("suburb", addr.get("neighbourhood", ""))
        parts = [p for p in [road, house, suburb] if p]
        return ", ".join(parts) if parts else data.get("display_name", "")[:120]
    except Exception as e:
        logger.debug("Reverse geocode failed for (%.4f, %.4f): %s", lat, lon, e)
        return None


# ---------------------------------------------------------------------------
# API fetching
# ---------------------------------------------------------------------------


def _fetch_page(start: int = 0, rows: int = PAGE_SIZE) -> tuple[list[dict], int]:
    """Fetch one page of solares from the API.

    Returns (records, total_count). Records have keys: id, status_code, status,
    geometry (Point with UTM coords), polygon (boundary).
    """
    try:
        resp = SESSION.get(
            API_BASE,
            params={
                "rows": rows,
                "start": start,
                "q": "status_code==4",  # only "Solar" (vacant private plots)
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", []), data.get("totalCount", 0)
    except requests.RequestException as e:
        logger.error("Solares API fetch failed (start=%d): %s", start, e)
        return [], 0


def _solar_to_id(solar_id: str) -> str:
    """Generate a stable source_id from the solar's API id."""
    return hashlib.md5(f"solar:{solar_id}".encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Main scraper
# ---------------------------------------------------------------------------


def scrape_solares(geocode: bool = False) -> list[EdictRecord]:
    """Scrape Zaragoza vacant plots API.

    Args:
        geocode: If True, reverse-geocode each plot's coordinates via Nominatim.
                 This is SLOW (~1 req/sec * 2400 records = 40 min) so it's
                 off by default. When False, address is set to the lat/lon string.

    Returns:
        List of EdictRecord objects with source="solares".
    """
    all_records: list[EdictRecord] = []
    seen_ids: set[str] = set()

    results, total = _fetch_page(0, PAGE_SIZE)
    if not results:
        logger.warning("Solares: no results from API")
        return []

    logger.info("Solares API: %d total vacant plots (status_code==4)", total)

    # Process first page
    page = 0
    while True:
        if page > 0:
            results, _ = _fetch_page(page * PAGE_SIZE, PAGE_SIZE)
            if not results:
                break

        new_on_page = 0
        for rec in results:
            solar_id = rec.get("id", "")
            if not solar_id or solar_id in seen_ids:
                continue
            seen_ids.add(solar_id)

            # Skip non-Solar (shouldn't happen with q filter, but be safe)
            if rec.get("status") != "Solar":
                continue

            # Extract UTM coordinates from geometry
            geom = rec.get("geometry", {})
            coords = geom.get("coordinates", [])
            if len(coords) < 2:
                continue

            easting, northing = coords[0], coords[1]
            lat, lon = _utm_to_latlon(easting, northing)

            # Build address
            if geocode:
                address = _reverse_geocode(lat, lon)
                if not address:
                    address = f"Zaragoza ({lat:.5f}, {lon:.5f})"
                time.sleep(1.1)  # Nominatim rate limit
            else:
                address = f"Zaragoza ({lat:.5f}, {lon:.5f})"

            source_id = _solar_to_id(solar_id)
            record = EdictRecord(
                source="solares",
                source_id=source_id,
                edict_type="solar_vacante",
                source_url=f"https://www.zaragoza.es/sede/servicio/solar/{solar_id}",
                address=address,
                localidad="Zaragoza",
            )
            all_records.append(record)
            new_on_page += 1

        logger.info(
            "Solares page %d (start=%d): %d new records",
            page, page * PAGE_SIZE, new_on_page,
        )

        page += 1
        if page >= MAX_PAGES or page * PAGE_SIZE >= total:
            break

        # Polite delay between API pages
        time.sleep(0.5)

    logger.info("Solares scrape complete: %d records", len(all_records))
    return all_records
