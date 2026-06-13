"""Zaragoza high-actionability pilot — phased, cost-capped, observable.

Phase 1: extract heirs/addresses (MiniMax) from Zaragoza edict + esquela leads.
Phase 2: contact-search (Perplexity) ONLY the leads that gained a real heir.

Reports the actionability lift and the contact hit-rate so you can compute ROI.
Exact spend: read the OpenRouter + Perplexity dashboards after the run.

    python tools/pilot_zaragoza.py --dry    # free: show candidate counts only
    python tools/pilot_zaragoza.py          # spend (capped): run both phases
"""

import sqlite3
import sys

from nadia_ai.config import DB_PATH

# Zaragoza, real inheritance signal (edicts) + esquelas that may name family.
# Pure Defunciones are excluded — their text is just "died in town X", no heirs.
ZAR_EXTRACT = (
    "localidad LIKE '%aragoza%' AND ("
    "sources LIKE '%BOE%' OR sources LIKE '%Tabl%' OR sources LIKE '%BOP%' "
    "OR sources LIKE '%BOA%' OR sources LIKE '%Esquela%')"
)
ZAR_ENRICH = "localidad LIKE '%aragoza%' AND COALESCE(heir_name,'') != ''"

EXTRACT_LIMIT = 40   # ~<= $0.40 on M3; the keyword funnel skips no-signal leads for free
ENRICH_LIMIT = 15    # ~$0.15 on Perplexity


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _count(c, where):
    return c.execute(f"SELECT COUNT(*) n FROM leads WHERE {where}").fetchone()["n"]


def _heir_count(c, where):
    return c.execute(
        f"SELECT COUNT(*) n FROM leads WHERE {where} AND COALESCE(heir_name,'') != ''"
    ).fetchone()["n"]


def main(dry: bool):
    c = _conn()
    print("=" * 66)
    print(f"Zaragoza pilot {'(DRY — no spend)' if dry else '(LIVE — will spend, capped)'}")
    print("=" * 66)

    pending = _count(c, f"ai_extraction_done = 0 AND ({ZAR_EXTRACT})")
    print(f"\nPhase 1 candidates (pending extraction, Zaragoza edicts+esquelas): {pending}")
    print(f"  will process up to {EXTRACT_LIMIT} (keyword funnel skips no-signal ones for free)")
    print(f"Already-actionable now (Zaragoza, has heir): {_heir_count(c, ZAR_ENRICH.split(' AND ')[0])}")

    if dry:
        already = _count(c, ZAR_ENRICH)
        print(f"\nPhase 2 candidates today (Zaragoza, heir, no contact): {already}")
        print("\nDry run only — re-run without --dry to spend.")
        return

    # ── Phase 1: extraction ────────────────────────────────────────────────
    print("\n--- Phase 1: extraction (MiniMax) ---")
    before_heirs = _heir_count(c, ZAR_EXTRACT)
    from nadia_ai.utils.extraction import run_heir_extraction
    extracted = run_heir_extraction(c, limit=EXTRACT_LIMIT, extra_where=ZAR_EXTRACT)
    after_heirs = _heir_count(c, ZAR_EXTRACT)
    tier_a = _count(c, f"({ZAR_EXTRACT}) AND tier = 'A'")
    print(f"  leads extracted (LLM calls): {extracted}")
    print(f"  leads with a heir name: {before_heirs} -> {after_heirs}  (+{after_heirs - before_heirs})")
    print(f"  Tier A in this scope now: {tier_a}")

    # ── Phase 2: contact search ────────────────────────────────────────────
    enrich_candidates = _count(c, ZAR_ENRICH)
    print(f"\n--- Phase 2: contact search (Perplexity), up to {ENRICH_LIMIT} ---")
    if enrich_candidates == 0:
        print("  no leads with a heir to enrich — stopping (no Perplexity spend).")
    else:
        from nadia_ai.enrich_contact import run_contact_enrichment
        found = run_contact_enrichment(c, limit=ENRICH_LIMIT, extra_where=ZAR_ENRICH)
        searched = min(enrich_candidates, ENRICH_LIMIT)
        print(f"  searched: {searched}   contacts found: {found}")
        print("\n  sample results:")
        for r in c.execute(
            f"""SELECT heir_name, localidad, contact_phone, contact_profile_url,
                       contact_confidence, contact_source_url
                FROM leads WHERE {ZAR_ENRICH.replace("COALESCE(heir_name,'') != ''", "COALESCE(contact_enriched_at,'') != ''")}
                LIMIT 10"""
        ).fetchall():
            got = r["contact_phone"] or r["contact_profile_url"] or "(none)"
            print(f"    {r['heir_name'][:26]:26}  {got[:40]:40}  [{r['contact_confidence']}]")

    print("\n" + "=" * 66)
    print("Done. For EXACT spend, check the OpenRouter + Perplexity dashboards.")
    print("=" * 66)


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
