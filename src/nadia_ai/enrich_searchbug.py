"""Heir-phone enrichment via Searchbug (opt-in, pay-as-you-go).

Searchbug is a pay-per-lookup people-search / phone-append / phone-validation API.
We use it to TRY to find a phone for a named heir of a Tier A/B lead. Honest caveats:
  * Searchbug's coverage is US-centric (its data is US consumer / phone-validation /
    DNC / reassigned-number). For a Spanish heir whose only data is "name + city" the
    yield will be very low — pay-as-you-go just means you pay per query instead of a
    subscription, not that the data exists. Results are stored as LOW confidence
    (contact_source='searchbug').
  * Storing/contacting EU personal phone data needs a lawful basis under GDPR.

OFF by default. Needs SEARCHBUG_API_KEY (+ SEARCHBUG_CO_CODE) and RESOLVE_HEIR_PHONE=1.
Fully defensive: any API error skips the lead and never breaks the pipeline.
Self-documenting — logs the first response's keys so the exact field mapping (and the
right TYPE/endpoint) can be confirmed on the runner against a real response.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time

import requests

from nadia_ai.config import (
    RESOLVE_HEIR_PHONE,
    SEARCHBUG_API_KEY,
    SEARCHBUG_API_URL,
    SEARCHBUG_CO_CODE,
    SEARCHBUG_TYPE,
)
from nadia_ai.enrich_contact import _normalize_es_phone

logger = logging.getLogger("nadia_ai.searchbug")

SESSION = requests.Session()
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{6,}\d")


def _clean_phone(raw: str) -> str:
    """Prefer a 9-digit Spanish number; otherwise fall back to a digits-only string
    of a plausible length (Searchbug may return non-ES numbers and the client still
    wants whatever number it finds). Returns '' if it isn't phone-shaped."""
    es = _normalize_es_phone(raw)
    if es:
        return es
    m = _PHONE_RE.search(str(raw or ""))
    if not m:
        return ""
    digits = re.sub(r"\D", "", m.group(0))
    return digits if 7 <= len(digits) <= 15 else ""


def _split_name(full: str) -> tuple[str, str]:
    """Best-effort split of a Spanish full name into (first, last). Spanish names are
    'NOMBRE APELLIDO1 APELLIDO2'; Searchbug wants FNAME/LNAME. We send the first token
    as first name and the rest as last name (covers the common two-surname case)."""
    parts = [p for p in str(full or "").split() if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _lookup(name: str, location: str) -> dict | None:
    """One Searchbug people-search lookup by name (+ location). Returns the JSON
    payload, retrying on rate-limit / transient TLS errors."""
    fname, lname = _split_name(name)
    params = {
        "CO_CODE": SEARCHBUG_CO_CODE,
        "PASS": SEARCHBUG_API_KEY,
        "TYPE": SEARCHBUG_TYPE,  # e.g. 'ppl' (people search) — confirm against your plan
        "FORMAT": "JSON",
        "FNAME": fname,
        "LNAME": lname,
    }
    if location:
        # Searchbug is US-addressed; we pass the Spanish locality as CITY so the field
        # is populated. (Coverage caveat applies — see module docstring.)
        params["CITY"] = location
    headers = {"Accept": "application/json"}
    for attempt in range(3):
        try:
            resp = SESSION.get(SEARCHBUG_API_URL, params=params, headers=headers, timeout=25)
            if resp.status_code in (200, 201):
                try:
                    return resp.json()
                except ValueError:
                    # Some Searchbug endpoints answer XML even with FORMAT=JSON; surface
                    # the raw text so _phones_from can still scan it for a number.
                    return {"_raw_text": resp.text}
            if resp.status_code == 429:  # rate limited
                time.sleep(2 * (attempt + 1))
                continue
            return None
        except (requests.RequestException, ValueError) as e:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            logger.info("Searchbug lookup failed for %r: %s", name[:40], e)
            return None
    return None


def _phones_from(data: dict | None) -> list[str]:
    """Pull phone numbers from an unknown Searchbug response shape.

    Handles `phone`/`phones`/`phone_number(s)` as strings or dicts ({'number':..}),
    a raw-text fallback (XML), and any phone-ish field scanned for a phone-shaped value."""
    if not isinstance(data, dict):
        return []
    out: list[str] = []

    def _add(v):
        if isinstance(v, str) and _PHONE_RE.search(v):
            out.append(v)
        elif isinstance(v, dict):
            for k in ("number", "phone", "value", "raw", "Phone", "PHONE"):
                if isinstance(v.get(k), str) and _PHONE_RE.search(v[k]):
                    out.append(v[k])
                    break

    for key in ("phone", "phones", "phone_number", "phone_numbers", "PHONE", "Phone"):
        v = data.get(key)
        if isinstance(v, list):
            for item in v:
                _add(item)
        elif v is not None:
            _add(v)
    # Raw-text fallback (XML body) — scan the whole string for phone-shaped tokens.
    raw = data.get("_raw_text")
    if isinstance(raw, str):
        out.extend(m.group(0) for m in _PHONE_RE.finditer(raw))
    # Last resort: any top-level string field whose name mentions phone.
    for k, v in data.items():
        if "phone" in k.lower() and k not in ("phone", "phones", "phone_number", "phone_numbers"):
            _add(v)
    # De-dup, preserve order.
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def resolve_heir_phones(conn: sqlite3.Connection, limit: int = 50) -> int:
    """Fill `contact_phone` for Tier A/B leads that have a named heir but no phone,
    by querying Searchbug. Returns the count of leads that got a phone."""
    if not (RESOLVE_HEIR_PHONE and SEARCHBUG_API_KEY):
        logger.info(
            "Searchbug heir-phone disabled (set RESOLVE_HEIR_PHONE=1 + SEARCHBUG_API_KEY)"
        )
        return 0
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, heir_name, localidad, region FROM leads
        WHERE tier IN ('A', 'B') AND heir_name IS NOT NULL AND heir_name != ''
          AND (contact_phone IS NULL OR contact_phone = '')
        ORDER BY tier ASC, first_seen_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    found = 0
    logged = False
    for row in rows:
        location = (row["localidad"] or row["region"] or "").strip()
        data = _lookup(row["heir_name"], location)
        if isinstance(data, dict) and not logged:
            logger.info("Searchbug sample response keys: %s", list(data.keys()))
            logged = True
        phones = _phones_from(data)
        phone = next((_clean_phone(p) for p in phones if _clean_phone(p)), "")
        if not phone:
            continue
        conn.execute(
            """UPDATE leads SET
                 contact_phone = ?,
                 contact_source = 'searchbug',
                 contact_confidence = 'heir-searchbug (low)',
                 contact_enriched_at = datetime('now'),
                 last_updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (phone, row["id"]),
        )
        conn.commit()
        found += 1
        logger.info("Searchbug: heir phone for lead %d (%s)", row["id"], row["heir_name"][:40])

    logger.info("Searchbug heir-phone: %d/%d leads got a phone", found, len(rows))
    return found
