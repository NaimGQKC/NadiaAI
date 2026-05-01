"""Pipeline entry point: python -m nadia_ai"""

import logging
import sys
import time
from datetime import UTC, datetime

from nadia_ai.db import get_connection, init_db, purge_expired_persons
from nadia_ai.logging_config import setup_logging

logger = logging.getLogger("nadia_ai.run")


def run_pipeline() -> dict:
    """Execute the full daily pipeline. Returns a summary dict."""
    start = time.monotonic()
    structured = "--cron" in sys.argv
    setup_logging(structured=structured)

    logger.info("Pipeline started at %s", datetime.now(UTC).isoformat())

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
        "enriched": 0,
        "subastas_enriched": 0,
        "obras_enriched": 0,
        "leads_today": 0,
        "errors": [],
    }

    # Step 2: Scrape Tablón de Edictos (Zaragoza city)
    try:
        from nadia_ai.scrapers.tablon import scrape_tablon

        tablon_records = scrape_tablon()
        summary["tablon_new"] = len(tablon_records)
        logger.info("Tablón: %d new records", len(tablon_records))
    except Exception as e:
        logger.error("Tablón scraper failed: %s", e)
        summary["errors"].append(f"tablon: {e}")
        tablon_records = []

    # Step 3: Scrape BOA (Boletín Oficial de Aragón)
    try:
        from nadia_ai.scrapers.boa import scrape_boa

        boa_records = scrape_boa()
        summary["boa_new"] = len(boa_records)
        logger.info("BOA: %d new records", len(boa_records))
    except Exception as e:
        logger.error("BOA scraper failed: %s", e)
        summary["errors"].append(f"boa: {e}")
        boa_records = []

    # Step 3b: Scrape BOP Zaragoza (province-wide edicts)
    try:
        from nadia_ai.scrapers.bop import scrape_bop

        bop_records = scrape_bop()
        summary["bop_new"] = len(bop_records)
        logger.info("BOP: %d new records", len(bop_records))
    except Exception as e:
        logger.error("BOP scraper failed: %s", e)
        summary["errors"].append(f"bop: {e}")
        bop_records = []

    # Step 3c: Scrape BOE (TEJU judicial edicts + Section V state-as-heir)
    try:
        from nadia_ai.scrapers.boe import scrape_boe

        boe_records = scrape_boe()
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

    all_records = tablon_records + boa_records + bop_records + boe_records + borme_records

    # Step 4: Enrich via Catastro and persist
    try:
        from nadia_ai.catastro import enrich_and_persist

        enriched = enrich_and_persist(conn, all_records)
        summary["enriched"] = enriched
        logger.info("Enriched %d records via Catastro", enriched)
    except Exception as e:
        logger.error("Catastro enrichment failed: %s", e)
        summary["errors"].append(f"catastro: {e}")

    # Step 5: Merge leads (dedup, tier classification, outreach flags)
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

    # Step 5b: Enrichment — cross-join subastas and obras data to leads
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
        logger.info("Enrichment: %d subastas, %d obras matched", subastas_enriched, obras_enriched)
    except Exception as e:
        logger.error("Enrichment failed (non-critical): %s", e)
        summary["errors"].append(f"enrichment: {e}")

    # Step 6: Compute today's leads and deliver
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
