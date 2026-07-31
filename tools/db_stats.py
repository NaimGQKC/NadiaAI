"""Read-only coverage analysis of the live leads DB — AGGREGATE COUNTS ONLY.

Prints total leads, tier distribution (A/B/C/X), recency, heir coverage, address
coverage (honest: city-only vs real street-level vs Catastro-resolved), contact
routes, notarial extraction yield, dedup ratio, and the outreach_log outcome funnel.

NEVER prints a name, email, phone, or address — only COUNT(*)s — so it is safe to
emit into a CI job log. Never writes to the DB (SELECT-only).

    NADIA_DB_PATH=...  python tools/db_stats.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from nadia_ai.config import DB_PATH  # noqa: E402


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cols = {r[1] for r in conn.execute("PRAGMA table_info(leads)").fetchall()}
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    def has(*c) -> bool:
        return all(x in cols for x in c)

    def n(pred: str) -> int:
        try:
            return conn.execute(f"SELECT COUNT(*) FROM leads WHERE {pred}").fetchone()[0]
        except sqlite3.OperationalError as e:
            return -1  # column missing / bad predicate — surfaced as n/a

    def line(label: str, val, denom: int | None = None) -> None:
        if val == -1:
            print(f"  {label:38} n/a (no column)")
            return
        pct = f"  ({100*val/denom:.1f}%)" if denom else ""
        print(f"  {label:38} {val:>7}{pct}")

    total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    bar = "=" * 64
    print(bar)
    print(f"  NadiaAI DB coverage — {DB_PATH}")
    print(f"  leads columns present: {len(cols)}   tables: {len(tables)}")
    print(bar)

    print(f"\nTOTAL LEADS: {total}")

    print("\n[Tier distribution]")
    for r in conn.execute("SELECT COALESCE(tier,'(null)') t, COUNT(*) k FROM leads GROUP BY t ORDER BY k DESC"):
        line(f"tier {r['t']}", r["k"], total)

    print("\n[Recency]")
    if "first_seen_at" in cols:
        line("first-seen last 7 days", n("first_seen_at >= datetime('now','-7 days')"), total)
        line("first-seen last 30 days", n("first_seen_at >= datetime('now','-30 days')"), total)
    else:
        print("  first_seen_at column absent — recency n/a")

    print("\n[Heir coverage]")
    line("named heir (heir_name<>'')", n("COALESCE(heir_name,'')<>''"), total)
    if "heir_names_json" in cols:
        line("multi-heir (>=2 names)", n("heir_names_json LIKE '%,%'"), total)
    if "ai_extraction_done" in cols:
        line("extraction done", n("ai_extraction_done=1"), total)
        line("extraction pending", n("ai_extraction_done=0"), total)

    print("\n[Address coverage — honest]")
    if "direccion" in cols:
        line("any direccion (incl. city-only)", n("COALESCE(direccion,'')<>''"), total)
        line("street-level (has a number)", n("direccion GLOB '*[0-9]*'"), total)
    rc_pred = " OR ".join(f"COALESCE({c},'')<>''" for c in ("ref_catastral", "referencia_catastral") if c in cols)
    if rc_pred:
        line("has Catastro ref", n(rc_pred), total)
    if "direccion" in cols and rc_pred:
        line("postal-actionable (number OR RC)", n(f"direccion GLOB '*[0-9]*' OR ({rc_pred})"), total)

    print("\n[Contact routes]")
    for c, lbl in (("contact_email", "email present"),
                   ("contact_phone", "phone present"),
                   ("contact_profile_url", "profile URL present")):
        if c in cols:
            line(lbl, n(f"COALESCE({c},'')<>''"), total)
    if "contact_source" in cols:
        line("  source='pdl'", n("contact_source='pdl'"), total)
        line("  source='juzgado'/'notaria'", n("contact_source IN ('juzgado','notaria')"), total)
    any_contact = " OR ".join(f"COALESCE({c},'')<>''" for c in
                              ("contact_email", "contact_phone", "contact_profile_url") if c in cols)
    if any_contact:
        line("ANY contact route", n(any_contact), total)

    print("\n[Notarial extraction yield]")
    if "sources" in cols:
        notarial = n("sources LIKE '%Notarial%'")
        nheir = n("sources LIKE '%Notarial%' AND COALESCE(heir_name,'')<>''")
        line("notarial leads", notarial)
        line("notarial + named heir", nheir)
        if notarial and notarial > 0:
            print(f"  {'notarial yield':38} {100*nheir/notarial:>6.1f}%  (= 100*{nheir}/{notarial})")

    print("\n[Deduplication]")
    print(f"  {'unique leads':38} {total:>7}")
    if "lead_edicts" in tables:
        le = conn.execute("SELECT COUNT(*) FROM lead_edicts").fetchone()[0]
        print(f"  {'lead_edicts links (raw folded in)':38} {le:>7}")
        if total:
            print(f"  {'ratio lead_edicts : leads':38} {le/total:>7.2f} : 1")
    if "source_urls" in cols:
        raw = 0
        for (su,) in conn.execute("SELECT source_urls FROM leads"):
            try:
                raw += len(json.loads(su or "[]"))
            except (TypeError, ValueError):
                pass
        print(f"  {'sum(source_urls) raw records':38} {raw:>7}")
        if total:
            print(f"  {'ratio raw source_urls : leads':38} {raw/total:>7.2f} : 1")

    print("\n[Outreach outcomes]")
    if "outreach_log" in tables:
        tot = conn.execute("SELECT COUNT(*) FROM outreach_log").fetchone()[0]
        print(f"  {'outreach_log rows':38} {tot:>7}")
        distinct = conn.execute("SELECT COUNT(DISTINCT lead_id) FROM outreach_log").fetchone()[0]
        print(f"  {'distinct leads contacted':38} {distinct:>7}")
        try:
            for r in conn.execute("SELECT outcome, COUNT(*) k FROM outreach_log GROUP BY outcome ORDER BY k DESC"):
                print(f"    outcome={str(r['outcome']):16} {r['k']:>5}")
        except sqlite3.OperationalError:
            print("  (no 'outcome' column)")
    else:
        print("  outreach_log table absent — 0 outreach ever recorded")

    print("\n" + bar)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
