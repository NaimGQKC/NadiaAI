"""Territory report: leads per comunidad autónoma, split by tier.

Answers the commercial question "where do we already have enough quality leads to
sell?" — grouped by CCAA, with a "ready" column that counts leads an agent could
actually act on today (Tier A/B with a named heir AND a contact path: a
street-level address or a phone).

Runs against the persistent DB (honours NADIA_DB_PATH). Print-only, no writes.

    python tools/territory_report.py
"""

import json
import sqlite3
import sys
from collections import defaultdict

DB = "nadia_ai.db"
try:
    from nadia_ai.config import DB_PATH
    DB = str(DB_PATH)
except Exception:
    pass

from nadia_ai.utils.regions import ccaa_for  # noqa: E402


def _has_number(s: str | None) -> bool:
    return bool(s) and any(ch.isdigit() for ch in s)


def main() -> int:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT tier, region, localidad, direccion, contact_phone, heir_name "
        "FROM leads"
    ).fetchall()
    if not rows:
        print(f"No leads in {DB} (empty DB).")
        return 0

    # ccaa -> tier -> count, plus ccaa -> ready count
    by_ccaa: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    ready: dict[str, int] = defaultdict(int)
    by_prov: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for r in rows:
        ccaa = ccaa_for(r["region"], r["localidad"], r["direccion"])
        tier = (r["tier"] or "?").upper()
        by_ccaa[ccaa][tier] += 1
        prov = (r["region"] or r["localidad"] or "?").strip() or "?"
        by_prov[prov][tier] += 1
        has_heir = bool((r["heir_name"] or "").strip())
        has_contact = _has_number(r["direccion"]) or bool((r["contact_phone"] or "").strip())
        if tier in ("A", "B") and has_heir and has_contact:
            ready[ccaa] += 1

    tiers = ["A", "B", "C", "X"]

    def _ab(d):  # Tier A+B = the commercial pool (C/X are killed)
        return d.get("A", 0) + d.get("B", 0)

    print("\n=== LEADS POR COMUNIDAD AUTÓNOMA × TIER ===")
    print(f"DB: {DB} | total leads: {len(rows)}\n")
    hdr = f"{'Comunidad':<22} {'A':>5} {'B':>6} {'C':>6} {'X':>4} {'A+B':>6} {'Ready*':>7}"
    print(hdr)
    print("-" * len(hdr))
    for ccaa in sorted(by_ccaa, key=lambda c: _ab(by_ccaa[c]), reverse=True):
        d = by_ccaa[ccaa]
        print(f"{ccaa:<22} {d.get('A',0):>5} {d.get('B',0):>6} {d.get('C',0):>6} "
              f"{d.get('X',0):>4} {_ab(d):>6} {ready.get(ccaa,0):>7}")
    tot = {t: sum(d.get(t, 0) for d in by_ccaa.values()) for t in tiers}
    print("-" * len(hdr))
    print(f"{'TOTAL':<22} {tot['A']:>5} {tot['B']:>6} {tot['C']:>6} {tot['X']:>4} "
          f"{tot['A']+tot['B']:>6} {sum(ready.values()):>7}")
    print("\n*Ready = Tier A/B con heredero nombrado Y vía de contacto "
          "(dirección con número o teléfono) — comercializable hoy.")

    print("\n=== TOP 15 PROVINCIAS por A+B ===")
    print(f"{'Provincia':<22} {'A':>5} {'B':>6} {'A+B':>6}")
    print("-" * 41)
    top = sorted(by_prov, key=lambda p: _ab(by_prov[p]), reverse=True)[:15]
    for prov in top:
        d = by_prov[prov]
        print(f"{prov:<22} {d.get('A',0):>5} {d.get('B',0):>6} {_ab(d):>6}")

    # Machine-readable line for downstream parsing / metrics.
    payload = {
        "total": len(rows),
        "by_ccaa": {c: dict(d) for c, d in by_ccaa.items()},
        "ready": dict(ready),
    }
    print("\nTERRITORY_JSON " + json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
