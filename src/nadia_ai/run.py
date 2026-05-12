"""Pipeline entry point: python -m nadia_ai

Phase 2: Inheritance Capture Engine — Death & Heirs niche only.
Deleted sources: Solares, Licencias, Servihabitat.
New steps: LLM heir extraction (Ollama), Plusvalía Clock recomputation.
"""

import logging
import sys
import time
from datetime import UTC, datetime, timedelta

from nadia_ai.db import get_connection, init_db, purge_expired_persons
from nadia_ai.logging_config import setup_logging

logger = logging.getLogger("nadia_ai.run")


def run_pipeline(days: int = 90) -> dict:
    """Execute the full daily pipeline. Returns a summary dict."""
    start = time.monotonic()
    structured = "--cron" in sys.argv
    setup_logging(structured=structured)

    # Override days from CLI if present
    if "--days" in sys.argv:
        try:
            idx = sys.argv.index("--days")
            days = int(sys.argv[idx + 1])
        except (ValueError, IndexError):
            pass

    logger.info("Pipeline started for last %d days at %s", days, datetime.now(UTC).isoformat())

    # Step 1: Database setup and TTL cleanup
    conn = get_connection()
    init_db(conn)
    purged = purge_expired_persons(conn)
    logger.info("TTL cleanup: purged %d expired person records", purged)

    summary = {
        "started_at": datetime.now(UTC).isoformat(),
        "purged_persons": purged,
        "tablon_new": 0,
        "boa_new": 0,
        "bop_new": 0,
        "boe_new": 0,
        "borme_new": 0,
        "esquelas_new": 0,
        "defunciones_new": 0,
        "iesquelas_new": 0,
        "cee_new": 0,
        "traspasos_new": 0,
        "ite_new": 0,
        "subastas_leads_new": 0,
        "enriched": 0,
        "subastas_enriched": 0,
        "obras_enriched": 0,
        "deadlines_recomputed": 0,
        "heirs_extracted": 0,
        "leads_today": 0,
        "errors": [],
    }

    # Step 1b: Recompute Plusvalía Clock for all existing leads
    try:
        from nadia_ai.merge import recompute_all_deadlines, init_leads_schema

        init_leads_schema(conn)
        recomputed = recompute_all_deadlines(conn)
        summary["deadlines_recomputed"] = recomputed
        logger.info("Plusvalía Clock: recomputed %d lead deadlines", recomputed)
    except Exception as e:
        logger.error("Deadline recomputation failed: %s", e)
        summary["errors"].append(f"deadlines: {e}")

    # Calculate the cutoff date for scraping
    cutoff_date = datetime.now(UTC) - timedelta(days=days)

    # ── PRIMARY ADMIN SOURCES ──────────────────────────────────────

    # Step 2: Scrape Tablón de Edictos (Zaragoza city)
    tablon_records = []
    try:
        from nadia_ai.scrapers.tablon import scrape_tablon

        tablon_records = scrape_tablon(since=cutoff_date)
        summary["tablon_new"] = len(tablon_records)
        logger.info("Tablón: %d new records", len(tablon_records))
    except Exception as e:
        logger.error("Tablón scraper failed: %s", e)
        summary["errors"].append(f"tablon: {e}")

    # Step 3: Scrape BOA (Boletín Oficial de Aragón)
    boa_records = []
    try:
        from nadia_ai.scrapers.boa import scrape_boa

        boa_records = scrape_boa(since=cutoff_date)
        summary["boa_new"] = len(boa_records)
        logger.info("BOA: %d new records", len(boa_records))
    except Exception as e:
        logger.error("BOA scraper failed: %s", e)
        summary["errors"].append(f"boa: {e}")

    # Step 3b: Scrape BOP Zaragoza (province-wide edicts)
    bop_records = []
    try:
        from nadia_ai.scrapers.bop import scrape_bop

        bop_records = scrape_bop(since=cutoff_date)
        summary["bop_new"] = len(bop_records)
        logger.info("BOP: %d new records", len(bop_records))
    except Exception as e:
        logger.error("BOP scraper failed: %s", e)
        summary["errors"].append(f"bop: {e}")

    # Step 3c: Scrape BOE (TEJU judicial edicts + Section V state-as-heir)
    try:
        from nadia_ai.scrapers.boe import scrape_boe

        boe_records = scrape_boe(days=days)
        summary["boe_new"] = len(boe_records)
        logger.info("BOE: %d new records", len(boe_records))
    except Exception as e:
        logger.error("BOE scraper failed: %s", e)
        summary["errors"].append(f"boe: {e}")
        boe_records = []

    # Step 3d: Scrape BORME (company death/dissolution events)
    borme_records = []
    try:
        from nadia_ai.scrapers.borme import scrape_borme

        borme_records = scrape_borme()
        summary["borme_new"] = len(borme_records)
        logger.info("BORME: %d new records", len(borme_records))
    except Exception as e:
        logger.error("BORME scraper failed: %s", e)
        summary["errors"].append(f"borme: {e}")

    # ── EARLY-SIGNAL SOURCES (Obituaries) ──
    esquelas_records = []
    try:
        from nadia_ai.scrapers.esquelas import scrape_esquelas

        esquelas_records = scrape_esquelas(since=cutoff_date)
        summary["esquelas_new"] = len(esquelas_records)
        logger.info("Esquelas: %d new records", len(esquelas_records))
    except Exception as e:
        logger.error("Esquelas scraper failed: %s", e)
        summary["errors"].append(f"esquelas: {e}")

    # Step 4b: Scrape defunciones.es (second obituary source with municipality data)
    defunciones_records = []
    try:
        from nadia_ai.scrapers.defunciones import scrape_defunciones

        defunciones_records = scrape_defunciones(since=cutoff_date)
        summary["defunciones_new"] = len(defunciones_records)
        logger.info("Defunciones: %d new records", len(defunciones_records))
    except Exception as e:
        logger.error("Defunciones scraper failed: %s", e)
        summary["errors"].append(f"defunciones: {e}")

    # Step 4c: Scrape iEsquelas.com (third obituary source — aggregator)
    iesquelas_records = []
    try:
        from nadia_ai.scrapers.iesquelas import scrape_iesquelas

        iesquelas_records = scrape_iesquelas(since=cutoff_date)
        summary["iesquelas_new"] = len(iesquelas_records)
        logger.info("iEsquelas: %d new records", len(iesquelas_records))
    except Exception as e:
        logger.error("iEsquelas scraper failed: %s", e)
        summary["errors"].append(f"iesquelas: {e}")

    # ── CAPEX SIGNALS ──────────────────────────────────────────────

    # Step 5a: Scrape CEE Aragón energy certificates
    cee_records = []
    try:
        from nadia_ai.scrapers.cee import scrape_cee

        cee_records = scrape_cee()
        summary["cee_new"] = len(cee_records)
        logger.info("CEE: %d new records", len(cee_records))
    except Exception as e:
        logger.error("CEE scraper failed: %s", e)
        summary["errors"].append(f"cee: {e}")

    # Step 5b: Scrape Traspasos Aragón business transfers
    traspasos_records = []
    try:
        from nadia_ai.scrapers.traspasos import scrape_traspasos

        traspasos_records = scrape_traspasos()
        summary["traspasos_new"] = len(traspasos_records)
        logger.info("Traspasos: %d new records", len(traspasos_records))
    except Exception as e:
        logger.error("Traspasos scraper failed: %s", e)
        summary["errors"].append(f"traspasos: {e}")

    # Step 5c: Scrape ITE Zaragoza building inspections (desfavorable only)
    ite_records = []
    try:
        from nadia_ai.scrapers.ite import scrape_ite

        ite_records = scrape_ite()
        summary["ite_new"] = len(ite_records)
        logger.info("ITE: %d new records", len(ite_records))
    except Exception as e:
        logger.error("ITE scraper failed: %s", e)
        summary["errors"].append(f"ite: {e}")

    # ── DISTRESS SIGNALS ───────────────────────────────────────────

    # Step 5d: Scrape Subastas BOE (Tier X — judicial auctions)
    subastas_lead_records = []
    try:
        from nadia_ai.scrapers.subastas import scrape_subastas_leads

        subastas_lead_records = scrape_subastas_leads()
        summary["subastas_leads_new"] = len(subastas_lead_records)
        logger.info("Subastas leads: %d new records", len(subastas_lead_records))
    except Exception as e:
        logger.error("Subastas lead gen failed: %s", e)
        summary["errors"].append(f"subastas_leads: {e}")


    # ── AGGREGATE & PROCESS ────────────────────────────────────────

    # ── IDENTITY RESOLUTION (Early Signals) ──
    # For obituary leads, try to find addresses to move them to Tier A
    try:
        from nadia_ai.utils.resolution import resolve_identity
        
        resolved_count = 0
        for record in esquelas_records + defunciones_records + iesquelas_records:
            if record.address: continue # Already has address
            
            # This is where the magic happens: Name + City -> Address
            # Currently a placeholder in resolution.py, but structure is ready
            res = resolve_identity(record.causante, record.localidad)
            if res and res.get("address"):
                record.address = res["address"]
                record.referencia_catastral = res.get("referencia_catastral")
                resolved_count += 1
        
        if resolved_count > 0:
            logger.info("Identity Resolution: resolved %d addresses from obituaries", resolved_count)
    except Exception as e:
        logger.error("Identity resolution failed: %s", e)

    all_records = (
        tablon_records + boa_records + bop_records + boe_records + borme_records
        + esquelas_records + defunciones_records + iesquelas_records
        + cee_records + traspasos_records + ite_records + subastas_lead_records
    )

    # Step 6: Enrich via Catastro and persist
    try:
        from nadia_ai.catastro import enrich_and_persist

        enriched = enrich_and_persist(conn, all_records)
        summary["enriched"] = enriched
        logger.info("Enriched %d records via Catastro", enriched)
    except Exception as e:
        logger.error("Catastro enrichment failed: %s", e)
        summary["errors"].append(f"catastro: {e}")

    # Step 7: Merge leads (dedup, tier classification, Plusvalía Clock)
    try:
        from nadia_ai.merge import merge_leads

        merge_stats = merge_leads(conn, all_records)
        summary["leads_created"] = merge_stats["created"]
        summary["leads_merged"] = merge_stats["merged"]
        summary["leads_skipped"] = merge_stats["skipped"]
        logger.info(
            "Merge: %d created, %d merged, %d skipped",
            merge_stats["created"],
            merge_stats["merged"],
            merge_stats["skipped"],
        )
    except Exception as e:
        logger.error("Lead merge failed: %s", e)
        summary["errors"].append(f"merge: {e}")

    # Step 7b: LLM Heir Extraction (Ollama — optional, graceful degradation)
    try:
        from nadia_ai.utils.extraction import run_heir_extraction

        extracted = run_heir_extraction(conn)
        summary["heirs_extracted"] = extracted
        logger.info("LLM heir extraction: %d leads enriched", extracted)
    except Exception as e:
        logger.error("LLM extraction failed (non-critical): %s", e)
        summary["errors"].append(f"llm_extraction: {e}")

    # Step 7c: Enrichment — cross-join subastas and obras data to leads
    try:
        from nadia_ai.enrichment import (
            enrich_leads_from_obras,
            enrich_leads_from_subastas,
            fetch_obras,
            fetch_subastas,
            init_enrichment_schema,
        )

        init_enrichment_schema(conn)
        fetch_subastas(conn)
        fetch_obras(conn)
        subastas_enriched = enrich_leads_from_subastas(conn)
        obras_enriched = enrich_leads_from_obras(conn)
        summary["subastas_enriched"] = subastas_enriched
        summary["obras_enriched"] = obras_enriched
        logger.info("Enrichment: %d obras matched, %d subastas matched", obras_enriched, subastas_enriched)
    except Exception as e:
        logger.error("Enrichment failed (non-critical): %s", e)
        summary["errors"].append(f"enrichment: {e}")

    # Step 7d: Export to Phantombuster (Tier A only)
    try:
        from nadia_ai.export_for_enrichment import run_export
        run_export()
        logger.info("Phantombuster export complete")
    except Exception as e:
        logger.error("Phantombuster export failed: %s", e)
        summary["errors"].append(f"phantombuster: {e}")

    # Step 8: Compute today's leads and deliver
    try:
        from nadia_ai.delivery import compute_todays_leads, deliver

        leads = compute_todays_leads(conn)
        summary["leads_today"] = len(leads)
        summary["tier_a"] = sum(1 for l in leads if l.tier == "A")
        summary["tier_b"] = sum(1 for l in leads if l.tier == "B")
        logger.info("Today's leads: %d (A=%d, B=%d)", len(leads), summary["tier_a"], summary["tier_b"])

        deliver(leads, summary)
    except Exception as e:
        logger.error("Delivery failed: %s", e)
        summary["errors"].append(f"delivery: {e}")

    elapsed = time.monotonic() - start
    summary["elapsed_seconds"] = round(elapsed, 2)
    logger.info(
        "Pipeline finished in %.2fs — %d leads, %d errors",
        elapsed,
        summary["leads_today"],
        len(summary["errors"]),
    )

    conn.close()
    return summary


if __name__ == "__main__":
    run_pipeline()
