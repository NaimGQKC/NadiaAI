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

    # Step 2-5: Parallel Scraping
    import concurrent.futures
    
    cutoff_date = datetime.now(UTC) - timedelta(days=days)
    
    # Mapping of scraper keys to (module_path, function_name, args)
    scrapers_config = {
        "tablon": ("nadia_ai.scrapers.tablon", "scrape_tablon", {"since": cutoff_date}),
        "boa": ("nadia_ai.scrapers.boa", "scrape_boa", {"since": cutoff_date}),
        "bop": ("nadia_ai.scrapers.bop", "scrape_bop", {"since": cutoff_date}),
        "boe": ("nadia_ai.scrapers.boe", "scrape_boe", {"days": days}),
        "borme": ("nadia_ai.scrapers.borme", "scrape_borme", {}),
        "esquelas": ("nadia_ai.scrapers.esquelas", "scrape_esquelas", {"since": cutoff_date}),
        "defunciones": ("nadia_ai.scrapers.defunciones", "scrape_defunciones", {"since": cutoff_date}),
        "iesquelas": ("nadia_ai.scrapers.iesquelas", "scrape_iesquelas", {"since": cutoff_date}),
        "cee": ("nadia_ai.scrapers.cee", "scrape_cee", {}),
        "traspasos": ("nadia_ai.scrapers.traspasos", "scrape_traspasos", {}),
        "ite": ("nadia_ai.scrapers.ite", "scrape_ite", {}),
        "subastas_leads": ("nadia_ai.scrapers.subastas", "scrape_subastas_leads", {}),
    }

    all_records = []
    logger.info("Starting concurrent scrapers (ThreadPoolExecutor)...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_key = {}
        for key, (mod_path, func_name, kwargs) in scrapers_config.items():
            try:
                # Dynamic import
                module = __import__(mod_path, fromlist=[func_name])
                func = getattr(module, func_name)
                future = executor.submit(func, **kwargs)
                future_to_key[future] = key
            except Exception as e:
                logger.error("Failed to submit scraper %s: %s", key, e)
                summary["errors"].append(f"submit_{key}: {e}")

        for future in concurrent.futures.as_completed(future_to_key):
            key = future_to_key[future]
            try:
                records = future.result()
                all_records.extend(records)
                summary[f"{key}_new"] = len(records)
                logger.info("%s: %d new records", key.capitalize(), len(records))
            except Exception as e:
                logger.error("%s scraper failed: %s", key.capitalize(), e)
                summary["errors"].append(f"{key}: {e}")

    # ── AGGREGATE & PROCESS ────────────────────────────────────────

    # ── IDENTITY RESOLUTION (Early Signals) ──
    # For obituary leads, try to find addresses to move them to Tier A
    try:
        from nadia_ai.utils.resolution import resolve_identity
        
        # Scrapers that provide early signals for obituaries
        obituary_keys = ["esquelas", "defunciones", "iesquelas"]
        obituary_records = [r for r in all_records if getattr(r, "subsource", "") in obituary_keys]
        
        resolved_count = 0
        for record in obituary_records:
            if record.address: continue # Already has address
            
            res = resolve_identity(record.causante, record.localidad)
            if res and res.get("address"):
                record.address = res["address"]
                record.referencia_catastral = res.get("referencia_catastral")
                resolved_count += 1
        
        if resolved_count > 0:
            logger.info("Identity Resolution: resolved %d addresses from obituaries", resolved_count)
    except Exception as e:
        logger.error("Identity resolution failed: %s", e)

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
