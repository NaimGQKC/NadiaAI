"""Export named heirs as a Hunter.io bulk-enrichment CSV.

Hunter finds BUSINESS emails from `first_name + last_name + domain`. Our heirs are
private individuals with no company, so `domain` is blank and the expected match rate
is low — this exists to test that cheaply, not because it is the right tool.

Columns are Hunter's bulk Email Finder shape (first_name, last_name, domain) plus
context columns Hunter ignores but that let you join results back to the lead.

    HUNTER_SCOPE=zaragoza python tools/export_hunter.py
Env: HUNTER_SCOPE (default "" = all), HUNTER_TIERS (default "A,B"),
     HUNTER_OUT (default ./hunter_leads.csv), NADIA_DB_PATH
"""

from __future__ import annotations

import csv
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from nadia_ai.config import DB_PATH  # noqa: E402

import test_heir_phones as tp  # noqa: E402

SCOPE = (os.getenv("HUNTER_SCOPE") or "").strip().lower()
TIERS = [t.strip().upper() for t in os.getenv("HUNTER_TIERS", "A,B").split(",") if t.strip()]
OUT = os.getenv("HUNTER_OUT") or os.path.join(os.getcwd(), "hunter_leads.csv")


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ph = ",".join("?" for _ in TIERS)
    rows = conn.execute(
        f"""SELECT id, heir_name, localidad, region, tier, contact_email
            FROM leads
            WHERE heir_name IS NOT NULL AND heir_name != ''
              AND tier IN ({ph})""",
        tuple(TIERS),
    ).fetchall()

    out = []
    for r in rows:
        name = r["heir_name"] or ""
        # Same quality gate the enrichment uses: never ship junk names to a paid tool.
        if not tp._good_heir_name(name):
            continue
        if SCOPE and not tp._in_scope(r["region"] or "", r["localidad"] or "", SCOPE):
            continue
        if (r["contact_email"] or "").strip():   # already has an email — don't re-spend
            continue
        first, last = tp._split_name(name)
        if not (first and last):
            continue
        out.append({
            "first_name": first,
            "last_name": last,
            "domain": "",           # unknown: heirs are private individuals
            "company": "",
            "full_name": name,
            "city": r["localidad"] or "",
            "region": r["region"] or "",
            "lead_id": r["id"],
        })

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["first_name", "last_name", "domain", "company",
                                          "full_name", "city", "region", "lead_id"])
        w.writeheader()
        w.writerows(out)

    print(f"Hunter CSV: {len(out)} heirs (scope={SCOPE or 'all'}, tiers={'+'.join(TIERS)}) -> {OUT}")
    print("NOTE: 'domain' is empty — Hunter matches on name+domain, so hit rate will be low.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
