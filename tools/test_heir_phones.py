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


def _classify_pdl(data: dict) -> tuple[str, str]:
    """Return (result, value). result ∈ {number, flag_only, no_match}."""
    if not isinstance(data, dict):
        return "no_match", ""
    for key in ("phone_numbers", "mobile_phone"):
        v = data.get(key)
        if isinstance(v, list) and v:
            real = [x for x in v if isinstance(x, str) and _PHONE_RE.search(x)]
            if real:
                return "number", real[0]
            return "flag_only", "(list, no digits)"
        if isinstance(v, str) and _PHONE_RE.search(v):
            return "number", v
        if v is True:
            return "flag_only", "true (masked — paid PII required)"
    return "no_match", ""


def _lookup_pdl(name: str, locality: str, region: str) -> tuple[str, str, int]:
    """Returns (result, value, likelihood)."""
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
                body = r.json()
                res, val = _classify_pdl(body.get("data", {}))
                return res, val, body.get("likelihood", 0)
            if r.status_code == 404:
                return "no_match", "", 0  # PDL: no matching person, 0 credits
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            return f"http_{r.status_code}", (r.text[:120]), 0
        except (requests.RequestException, ValueError) as e:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            return "error", str(e)[:120], 0
    return "no_match", "", 0


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

    print(f"=== Heir-phone coverage test | provider={PROVIDER} | sample={len(rows)} | country={COUNTRY} ===\n")
    counts = {"number": 0, "flag_only": 0, "no_match": 0, "other": 0}
    for i, row in enumerate(rows, 1):
        name = row["heir_name"]
        loc = (row["localidad"] or "").strip()
        reg = (row["region"] or "").strip()
        res, val, lk = _lookup_pdl(name, loc, reg)
        bucket = res if res in counts else "other"
        counts[bucket] += 1
        print(f"{i:>3}. {name[:34]:34} | {loc[:18]:18} | lk={lk} | {res:10} | {val[:40]}")

    n = len(rows)
    have = counts["number"] + counts["flag_only"]  # provider HAS a phone (ceiling)
    print("\n=== SUMMARY ===")
    print(f"  provider HAS a phone (ceiling): {have}/{n}  ({100*have//n if n else 0}%)")
    print(f"    - real number returned:       {counts['number']}/{n}")
    print(f"    - masked flag only (paid):    {counts['flag_only']}/{n}")
    print(f"  no match at all:                {counts['no_match']}/{n}")
    if counts["other"]:
        print(f"  errors/other:                   {counts['other']}/{n}")
    print("\nInterpretation: 'ceiling' = what a PAID PII key could return. If the ceiling is")
    print("near 0, paying is pointless for this cohort. If it's high, a paid key is worth it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
