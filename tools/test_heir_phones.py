"""Empirical heir-phone COVERAGE test against the real heirs in the DB.

Runs on PEDRO (where the persistent DB lives). For a sample of real heirs it calls a
people-enrichment provider and reports how many the provider even HAS a phone for.

Why this works on a FREE key: People Data Labs returns PII fields as a boolean `true`
when your plan doesn't include the value (e.g. "phone_numbers": true means "we have a
number but upgrade to see it"). So even without paying we can measure the CEILING —
the fraction of our real heirs the provider could return a number for on a paid plan.
That is the go/no-go signal for whether paying is worth it.

Usage (on PEDRO, PowerShell):
    $env:HEIR_TEST_KEY="<api-key>"; $env:HEIR_TEST_SAMPLE="50"; python tools/test_heir_phones.py

Read-only: never writes to the DB. Provider defaults to PDL (pdl); proxycurl stub
included for when that key is available.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from nadia_ai.config import DB_PATH  # noqa: E402

PROVIDER = os.getenv("HEIR_TEST_PROVIDER", "pdl").lower()
API_KEY = os.getenv("HEIR_TEST_KEY", "")
SAMPLE = int(os.getenv("HEIR_TEST_SAMPLE", "50"))
COUNTRY = os.getenv("HEIR_TEST_COUNTRY", "Spain")

_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{6,}\d")


def _split_name(full: str) -> tuple[str, str]:
    parts = [p for p in str(full or "").split() if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _classify_field(data: dict, keys: tuple[str, ...], pattern: re.Pattern) -> str:
    """Classify a contact field across candidate keys → value|flag|none.
      value = an actual usable string is present (free-tier win!)
      flag  = provider HAS it but masks the value to a boolean `true` (paid PII)
      none  = provider has nothing (False / absent / 404)."""
    if not isinstance(data, dict):
        return "none"
    saw_flag = False
    for key in keys:
        v = data.get(key)
        if isinstance(v, list) and v:
            if any(isinstance(x, str) and pattern.search(x) for x in v):
                return "value"
            saw_flag = True
        elif isinstance(v, str) and pattern.search(v):
            return "value"
        elif v is True:
            saw_flag = True
    return "flag" if saw_flag else "none"


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_PHONE_KEYS = ("phone_numbers", "mobile_phone", "phone")
_EMAIL_KEYS = ("emails", "personal_emails", "recommended_personal_email", "work_email")


def _extract_value(data: dict, keys: tuple[str, ...], pattern: re.Pattern) -> str:
    """Return the first actual usable string across candidate keys (paid key), else ''."""
    if not isinstance(data, dict):
        return ""
    for key in keys:
        v = data.get(key)
        if isinstance(v, list):
            for x in v:
                if isinstance(x, str) and pattern.search(x):
                    return x
                if isinstance(x, dict):
                    for kk in ("address", "email", "value", "number"):
                        if isinstance(x.get(kk), str) and pattern.search(x[kk]):
                            return x[kk]
        elif isinstance(v, str) and pattern.search(v):
            return v
    return ""


def _lookup_pdl(name: str, locality: str, region: str, street: str = "") -> dict:
    """Returns a dict with phone/email class, actual email/phone VALUES (paid key),
    linkedin, matched_name, likelihood, and whether the call was a billable match."""
    fname, lname = _split_name(name)
    params = {
        "first_name": fname,
        "last_name": lname,
        "country": COUNTRY,
        "min_likelihood": 2,
    }
    if locality:
        params["locality"] = locality
    if region:
        params["region"] = region
    if street:
        params["street_address"] = street  # disambiguates same-name people
    headers = {"X-Api-Key": API_KEY}
    for attempt in range(3):
        try:
            r = requests.get(
                "https://api.peopledatalabs.com/v5/person/enrich",
                params=params, headers=headers, timeout=25,
            )
            if r.status_code == 200:
                body = r.json()
                data = body.get("data", {}) or {}
                return {
                    "phone": _classify_field(data, _PHONE_KEYS, _PHONE_RE),
                    "email": _classify_field(data, _EMAIL_KEYS, _EMAIL_RE),
                    "email_value": _extract_value(data, _EMAIL_KEYS, _EMAIL_RE),
                    "phone_value": _extract_value(data, _PHONE_KEYS, _PHONE_RE),
                    "linkedin": data.get("linkedin_url") or "",
                    "matched": (data.get("full_name") or "")[:28],
                    "lk": body.get("likelihood", 0),
                    "billable": True,  # a 200 match consumes 1 PDL credit
                }
            if r.status_code == 404:
                return {"phone": "none", "email": "none", "email_value": "", "phone_value": "",
                        "linkedin": "", "matched": "", "lk": 0, "billable": False}
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            return {"phone": f"http_{r.status_code}", "email": "none", "email_value": "",
                    "phone_value": "", "linkedin": "", "matched": "", "lk": 0, "billable": False}
        except (requests.RequestException, ValueError):
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            return {"phone": "error", "email": "none", "email_value": "", "phone_value": "",
                    "linkedin": "", "matched": "", "lk": 0, "billable": False}
    return {"phone": "none", "email": "none", "email_value": "", "phone_value": "",
            "linkedin": "", "matched": "", "lk": 0, "billable": False}


def _ensure_email_column(conn: sqlite3.Connection) -> None:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(leads)")]
    if "contact_email" not in cols:
        conn.execute("ALTER TABLE leads ADD COLUMN contact_email TEXT")
        conn.commit()


def main() -> int:
    # Windows consoles default to cp1252 and choke on non-ASCII names/box chars.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if not API_KEY:
        print("ERROR: set HEIR_TEST_KEY", file=sys.stderr)
        return 2
    if PROVIDER != "pdl":
        print(f"ERROR: provider {PROVIDER!r} not implemented yet (only 'pdl')", file=sys.stderr)
        return 2

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Prefer heirs WITH a street address (better match input), Tier A/B, newest first.
    rows = conn.execute(
        """
        SELECT id, heir_name, localidad, region, direccion FROM leads
        WHERE heir_name IS NOT NULL AND heir_name != ''
          AND tier IN ('A','B')
        ORDER BY (CASE WHEN direccion IS NOT NULL AND direccion != '' THEN 0 ELSE 1 END),
                 first_seen_at DESC
        LIMIT ?
        """,
        (SAMPLE,),
    ).fetchall()

    if not rows:
        print("No heirs in DB to test.")
        return 0

    _ensure_email_column(conn)
    max_matches = int(os.getenv("HEIR_MAX_MATCHES", "330"))  # credit safety cap (<350)

    print(f"=== Heir contact enrichment | provider={PROVIDER} | sample={len(rows)} | country={COUNTRY} ===")
    print(f"    credit cap: stop after {max_matches} matches | writes real emails to DB when found\n")
    ph = {"value": 0, "flag": 0, "none": 0, "other": 0}
    em = {"value": 0, "flag": 0, "none": 0, "other": 0}
    matched = 0
    written = 0
    found_emails: list[tuple] = []
    for i, row in enumerate(rows, 1):
        if matched >= max_matches:
            print(f"\n[credit cap reached: {matched} matches ~= credits — stopping]")
            break
        name = row["heir_name"]
        loc = (row["localidad"] or "").strip()
        reg = (row["region"] or "").strip()
        street = (row["direccion"] or "").strip()
        r = _lookup_pdl(name, loc, reg, street)
        ph[r["phone"] if r["phone"] in ph else "other"] += 1
        em[r["email"] if r["email"] in em else "other"] += 1
        if r["billable"]:
            matched += 1
        # Write any REAL email value (paid key) to the DB — low confidence, keep LinkedIn for verify.
        if r["email_value"]:
            conn.execute(
                """UPDATE leads SET
                     contact_email = ?,
                     contact_source = 'pdl',
                     contact_confidence = 'heir-pdl (low)',
                     contact_enriched_at = datetime('now'),
                     last_updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (r["email_value"], row["id"]),
            )
            conn.commit()
            written += 1
            found_emails.append((name, r["email_value"], r["linkedin"], r["matched"]))
        print(f"{i:>3}. {name[:28]:28} | {loc[:12]:12} | lk={r['lk']} | em:{r['email']:5} "
              f"| {r['email_value'][:30]:30} | -> {r['matched']}")

    n = i
    em_ceiling = em["value"] + em["flag"]
    pct = lambda x: f"{100*x//n if n else 0}%"
    print("\n=== SUMMARY ===")
    print(f"  heirs processed:                {n}")
    print(f"  billable matches (credits used):{matched}")
    print(f"  person matched at all:          {matched}/{n}  ({pct(matched)})")
    print(f"  EMAIL ceiling (has an email):   {em_ceiling}/{n}  ({pct(em_ceiling)})")
    print(f"  >>> REAL EMAILS WRITTEN TO DB:  {written}/{n}  ({pct(written)}) <<<")
    if found_emails:
        print("\n=== EMAILS FOUND (spot-check identity via matched name / LinkedIn) ===")
        for nm, mail, li, mtc in found_emails:
            print(f"  {nm[:34]:34} -> {mail}  | match={mtc} | {li}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
