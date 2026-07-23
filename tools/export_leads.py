"""Export enriched heir leads to a clean CSV for the client.

Pulls leads that carry a contact_email (the verified PDL enrichment) and writes a
tidy, client-ready CSV: one row per heir with the deceased, location, tier, the
email, and its confidence tier (high | medium). Read-only — never mutates the DB.

Usage (on PEDRO, where the persistent DB lives):
    python tools/export_leads.py                      # all CCAAs, emailed leads
    HEIR_EXPORT_CCAA=aragon python tools/export_leads.py
    HEIR_EXPORT_CONF=high  python tools/export_leads.py   # only high-confidence
    HEIR_EXPORT_OUT=C:\\path\\nadia_leads.csv python tools/export_leads.py

Env:
    HEIR_EXPORT_CCAA  restrict to a CCAA (e.g. "aragon"); default = all.
    HEIR_EXPORT_CONF  "high" to emit only high-confidence rows; default = high+medium.
    HEIR_EXPORT_OUT   output path; default = ./nadia_leads_export.csv
    NADIA_DB_PATH     persistent DB path (set by the workflow).
"""

from __future__ import annotations

import csv
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(__file__))  # tools/  (for test_heir_phones helpers)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from nadia_ai.config import DB_PATH  # noqa: E402

# Reuse the exact CCAA test the enrichment uses, so export scope == run scope.
import test_heir_phones as tp  # noqa: E402

CCAA = (os.getenv("HEIR_EXPORT_CCAA") or "").strip().lower()
CONF = (os.getenv("HEIR_EXPORT_CONF") or "").strip().lower()   # "" = high+medium
OUT = os.getenv("HEIR_EXPORT_OUT") or os.path.join(os.getcwd(), "nadia_leads_export.csv")

# Preferred columns, in client-facing order; only those present are emitted.
_WANT = [
    ("causante", "deceased"),
    ("heir_name", "heir"),
    ("localidad", "town"),
    ("region", "region"),
    ("tier", "tier"),
    ("contact_email", "email"),
    ("contact_confidence", "email_confidence"),
    ("date_of_death", "date_of_death"),
    ("referencia_catastral", "catastro_ref"),
    ("direccion", "property_address"),
]


def _cols(conn: sqlite3.Connection) -> set:
    return {r[1] for r in conn.execute("PRAGMA table_info(leads)").fetchall()}


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    have = _cols(conn)
    cols = [(c, label) for c, label in _WANT if c in have]
    if not cols:
        print(f"ERROR: no 'leads' table (or none of the expected columns) at {DB_PATH}. "
              "Run this on PEDRO where the persistent DB lives.", file=sys.stderr)
        return 2
    select = ", ".join(c for c, _ in cols)

    rows = conn.execute(
        f"""SELECT {select} FROM leads
            WHERE contact_email IS NOT NULL AND contact_email != ''
            ORDER BY contact_confidence DESC, region, localidad"""
    ).fetchall()

    def _keep(r: sqlite3.Row) -> bool:
        if CCAA and not tp._in_ccaa(r["region"] if "region" in r.keys() else "",
                                    r["localidad"] if "localidad" in r.keys() else "", CCAA):
            return False
        if CONF == "high":
            conf = (r["contact_confidence"] or "") if "contact_confidence" in r.keys() else ""
            return "high" in conf.lower()
        return True

    kept = [r for r in rows if _keep(r)]

    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:  # BOM => Excel opens UTF-8 clean
        w = csv.writer(f)
        w.writerow([label for _, label in cols])
        for r in kept:
            w.writerow([r[c] for c, _ in cols])

    n_high = sum(1 for r in kept
                 if "contact_confidence" in r.keys()
                 and "high" in (r["contact_confidence"] or "").lower())
    scope = CCAA or "all CCAAs"
    conf = CONF or "high+medium"
    print(f"Exported {len(kept)} emailed leads ({n_high} high, {len(kept) - n_high} medium) "
          f"[scope={scope}, conf={conf}] -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
