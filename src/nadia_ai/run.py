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
        "enriched": 0,
        "leads_today": 0,
        "errors": [],
    }

    # Step 2: Scrape Tablón de Edictos
    try:
        from nadia_ai.scrapers.tablon import scrape_tablon

        tablon_records = scrape_tablon()
        summary["tablon_new"] = len(tablon_records)
        logger.info("Tablón: %d new records", len(tablon_records))
    except Exception as e:
        logger.error("Tablón scraper failed: %s", e)
        summary["errors"].append(f"tablon: {e}")
        tablon_records = []

    # Step 3: Scrape BOA
    try:
        from nadia_ai.scrapers.boa import scrape_boa

        boa_records = scrape_boa()
        summary["boa_new"] = len(boa_records)
        logger.info("BOA: %d new records", len(boa_records))
    except Exception as e:
        logger.error("BOA scraper failed: %s", e)
        summary["errors"].append(f"boa: {e}")
        boa_records = []

    all_records = tablon_records + boa_records

    # Step 4: Enrich via Catastro and persist
    try:
        from nadia_ai.catastro import enrich_and_persist

        enriched = enrich_and_persist(conn, all_records)
        summary["enriched"] = enriched
        logger.info("Enriched %d records via Catastro", enriched)
    except Exception as e:
        logger.error("Catastro enrichment failed: %s", e)
        summary["errors"].append(f"catastro: {e}")

    # Step 5: Compute today's leads and deliver
    try:
        from nadia_ai.delivery import compute_todays_leads, deliver

        leads = compute_todays_leads(conn)
        summary["leads_today"] = len(leads)
        logger.info("Today's leads: %d", len(leads))

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
