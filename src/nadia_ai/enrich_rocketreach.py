"""Heir-phone enrichment via RocketReach (opt-in).

RocketReach is a commercial contact-data API (professional/LinkedIn-oriented). We use
it to TRY to find a phone for a named heir of a Tier A/B lead. Honest caveats:
  * Yield is low for private individuals (it's built for B2B prospecting), and a
    name + city alone can match the wrong person — so results are stored as
    LOW confidence (contact_source='rocketreach').
  * Storing/contacting EU personal phone data needs a lawful basis under GDPR.

OFF by default. Needs ROCKETREACH_API_KEY and RESOLVE_HEIR_PHONE=1. Fully defensive:
any API error skips the lead and never breaks the pipeline. Self-documenting — logs
the first response's keys so the phone-field mapping can be confirmed on the runner.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time

import requests

from nadia_ai.config import (
    RESOLVE_HEIR_PHONE,
    ROCKETREACH_API_KEY,
    ROCKETREACH_API_URL,
)
from nadia_ai.enrich_contact import _normalize_es_phone

logger = logging.getLogger("nadia_ai.rocketreach")

SESSION = requests.Session()
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{6,}\d")


def _lookup(name: str, location: str) -> dict | None:
    """One RocketReach person lookup by name (+ location). Returns the JSON profile,
    polling once if the API answers asynchronously ('searching')."""
    headers = {"Api-Key": ROCKETREACH_API_KEY, "Accept": "application/json"}
    params = {"name": name}
    if location:
        params["keyword"] = location  # location/company hint to disambiguate
    for attempt in range(3):
        try:
            resp = SESSION.get(ROCKETREACH_API_URL, params=params, headers=headers, timeout=25)
            if resp.status_code in (200, 201):
                data = resp.json()
                # Async: profile still being looked up → poll the same record once.
                if isinstance(data, dict) and data.get("status") in ("searching", "progress"):
                    time.sleep(3)
                    continue
                return data
            if resp.status_code == 429:  # rate limited
                time.sleep(2 * (attempt + 1))
                continue
            return None
        except (requests.RequestException, ValueError) as e:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            logger.info("RocketReach lookup failed for %r: %s", name[:40], e)
            return None
    return None


def _phones_from(data: dict | None) -> list[str]:
    """Pull phone numbers from an unknown RocketReach response shape.

    Handles `phones`/`phone_numbers` as lists of strings or of dicts ({'number':..}),
    and falls back to scanning any phone-ish field for a phone-shaped value."""
    if not isinstance(data, dict):
        return []
    out: list[str] = []

    def _add(v):
        if isinstance(v, str) and _PHONE_RE.search(v):
            out.append(v)
        elif isinstance(v, dict):
            for k in ("number", "phone", "value", "raw"):
                if isinstance(v.get(k), str) and _PHONE_RE.search(v[k]):
                    out.append(v[k])
                    break

    for key in ("phones", "phone_numbers", "mobile_phones", "phone"):
        v = data.get(key)
        if isinstance(v, list):
            for item in v:
                _add(item)
        elif v is not None:
            _add(v)
    # Last resort: any top-level string field whose name mentions phone.
    for k, v in data.items():
        if "phone" in k.lower():
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
    by querying RocketReach. Returns the count of leads that got a phone."""
    if not (RESOLVE_HEIR_PHONE and ROCKETREACH_API_KEY):
        logger.info(
            "RocketReach heir-phone disabled (set RESOLVE_HEIR_PHONE=1 + ROCKETREACH_API_KEY)"
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
            logger.info("RocketReach sample response keys: %s", list(data.keys()))
            logged = True
        phones = _phones_from(data)
        phone = next((_normalize_es_phone(p) for p in phones if _normalize_es_phone(p)), "")
        if not phone:
            continue
        conn.execute(
            """UPDATE leads SET
                 contact_phone = ?,
                 contact_source = 'rocketreach',
                 contact_confidence = 'heir-rocketreach (low)',
                 contact_enriched_at = datetime('now'),
                 last_updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (phone, row["id"]),
        )
        conn.commit()
        found += 1
        logger.info("RocketReach: heir phone for lead %d (%s)", row["id"], row["heir_name"][:40])

    logger.info("RocketReach heir-phone: %d/%d leads got a phone", found, len(rows))
    return found
