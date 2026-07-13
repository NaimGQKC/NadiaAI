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


def _lookup_pdl(name: str, locality: str, region: str) -> tuple[str, str, int]:
    """Returns (phone_class, email_class, likelihood). Each class ∈ value|flag|none.
    One enrich call yields both, so measuring email coverage costs no extra credits."""
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
    headers = {"X-Api-Key": API_KEY}
    for attempt in range(3):
        try:
            r = requests.get(
                "https://api.peopledatalabs.com/v5/person/enrich",
                params=params, headers=headers, timeout=25,
            )
            if r.status_code == 200:
                data = r.json().get("data", {}) or {}
                return (
                    _classify_field(data, _PHONE_KEYS, _PHONE_RE),
                    _classify_field(data, _EMAIL_KEYS, _EMAIL_RE),
                    r.json().get("likelihood", 0),
                )
            if r.status_code == 404:
                return "none", "none", 0  # no matching person, 0 credits
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            return f"http_{r.status_code}", "none", 0
        except (requests.RequestException, ValueError) as e:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            return "error", "none", 0
    return "none", "none", 0


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

    print(f"=== Heir contact coverage test | provider={PROVIDER} | sample={len(rows)} | country={COUNTRY} ===")
    print("    (value=usable string on THIS key | flag=has it, masked behind paid PII | none=nothing)\n")
    ph = {"value": 0, "flag": 0, "none": 0, "other": 0}
    em = {"value": 0, "flag": 0, "none": 0, "other": 0}
    matched = 0
    for i, row in enumerate(rows, 1):
        name = row["heir_name"]
        loc = (row["localidad"] or "").strip()
        reg = (row["region"] or "").strip()
        p_res, e_res, lk = _lookup_pdl(name, loc, reg)
        ph[p_res if p_res in ph else "other"] += 1
        em[e_res if e_res in em else "other"] += 1
        if lk:
            matched += 1
        print(f"{i:>3}. {name[:32]:32} | {loc[:16]:16} | lk={lk} | phone:{p_res:6} | email:{e_res:6}")

    n = len(rows)
    ph_ceiling = ph["value"] + ph["flag"]
    em_ceiling = em["value"] + em["flag"]
    pct = lambda x: f"{100*x//n if n else 0}%"
    print("\n=== SUMMARY ===")
    print(f"  person matched at all:          {matched}/{n}  ({pct(matched)})")
    print(f"  EMAIL ceiling (has an email):   {em_ceiling}/{n}  ({pct(em_ceiling)})")
    print(f"    - usable on this free key:    {em['value']}/{n}")
    print(f"    - masked, needs paid PII:     {em['flag']}/{n}")
    print(f"  PHONE ceiling (has a phone):    {ph_ceiling}/{n}  ({pct(ph_ceiling)})")
    print(f"    - usable on this free key:    {ph['value']}/{n}")
    print(f"    - masked, needs paid PII:     {ph['flag']}/{n}")
    print("\nInterpretation: 'ceiling' = what a PAID PII key could return for this cohort.")
    print("Near 0 → paying is pointless. High → a paid key is worth it (email vs phone separately).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
