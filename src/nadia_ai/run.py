"""Pipeline entry point: python -m nadia_ai

Phase 2: Inheritance Capture Engine — Death & Heirs niche only.
Deleted sources: Solares, Licencias, Servihabitat.
New steps: LLM heir extraction (Ollama), Plusvalía Clock recomputation.
"""

import logging
import sqlite3
import sys
import time
from datetime import UTC, datetime, timedelta

from nadia_ai.config import SPAIN_WIDE
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
        "enriched": 0,
        "obras_enriched": 0,
        "deadlines_recomputed": 0,
        "heirs_extracted": 0,
        "leads_today": 0,
        "errors": [],
    }

    # Soft wall-clock budget. The network-heavy enrichment steps below fetch one
    # BOE PDF per lead; with a large notarial backlog they can run for tens of
    # minutes and, on CI, overran the job timeout before the worklist email could
    # send. Once this budget is spent we skip the remaining *optional* enrichment
    # and go straight to merge/export/deliver (which never depend on it), so a
    # slow BOE day can never again starve delivery. 0 disables the guard.
    import os

    _budget_s = int(os.getenv("PIPELINE_SOFT_BUDGET_SECONDS", "1080"))  # 18 min
    _t0 = time.monotonic()

    def _budget_ok(step: str) -> bool:
        if _budget_s <= 0 or (time.monotonic() - _t0) < _budget_s:
            return True
        logger.warning(
            "Time budget (%ds) spent — skipping %s to guarantee delivery", _budget_s, step
        )
        return False

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
        "boe": ("nadia_ai.scrapers.boe", "scrape_boe", {"days": 10}), # Increased days for deeper scan
        # Autonomous-community bulletins (generic engine) — national-coverage edicts
        # with addresses, beyond Aragón's BOA. Endpoints validated on the runner.
        "bocm": ("nadia_ai.scrapers.boletin_autonomico", "scrape_bocm", {"since": cutoff_date}),
        "dogc": ("nadia_ai.scrapers.boletin_autonomico", "scrape_dogc", {"since": cutoff_date}),
        "boja": ("nadia_ai.scrapers.boletin_autonomico", "scrape_boja", {"since": cutoff_date}),
        "dogv": ("nadia_ai.scrapers.boletin_autonomico", "scrape_dogv", {"since": cutoff_date}),
        "borme": ("nadia_ai.scrapers.borme", "scrape_borme", {}),
        "esquelas": ("nadia_ai.scrapers.esquelas", "scrape_esquelas", {"since": cutoff_date}),
        "heraldo": ("nadia_ai.scrapers.heraldo", "scrape_heraldo", {"since": cutoff_date}),
        "defunciones": ("nadia_ai.scrapers.defunciones", "scrape_defunciones", {"since": cutoff_date}),
        "rememori": ("nadia_ai.scrapers.rememori", "scrape_rememori", {"since": cutoff_date}),
        "iesquelas": ("nadia_ai.scrapers.iesquelas", "scrape_iesquelas", {"since": cutoff_date}),
        "cee": ("nadia_ai.scrapers.cee", "scrape_cee", {}),
        "ite": ("nadia_ai.scrapers.ite", "scrape_ite", {}),
        # NOTE: subastas removed as a lead source per client decision — auctions
        # are buy-side deals for investors, not listing leads. Kept only as the
        # subasta_activa context flag in Step 7c enrichment (Tier X, no outreach).
        "traspasos": ("nadia_ai.scrapers.traspasos", "scrape_traspasos", {"zaragoza_only": not SPAIN_WIDE}),
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

        # Pass the budget deadline INTO extraction: each lead can cost ~80s on a
        # slow/garbled LLM (4 retries), so the per-lead loop must stop at the
        # deadline itself — checking only before the step (like the others) let it
        # overrun to 30 min in run #54.
        _deadline = (_t0 + _budget_s) if _budget_s > 0 else None
        extracted = run_heir_extraction(conn, deadline=_deadline) if _budget_ok("heir extraction") else 0
        summary["heirs_extracted"] = extracted
        logger.info("LLM heir extraction: %d leads enriched", extracted)
    except Exception as e:
        logger.error("LLM extraction failed (non-critical): %s", e)
        summary["errors"].append(f"llm_extraction: {e}")

    # Step 7b2: Resolve street addresses → referencia catastral via Catastro
    # callejero. Only leads with a street number are candidates (most obituary
    # leads are city-only and cannot get an RC). Runs after heir extraction so
    # any address extracted from edict text is included before the obras join.
    try:
        from nadia_ai.catastro import resolve_lead_addresses

        rc_resolved = resolve_lead_addresses(conn) if _budget_ok("catastro RC") else 0
        summary["rc_resolved"] = rc_resolved
        logger.info("Catastro address→RC: %d leads resolved", rc_resolved)
    except Exception as e:
        logger.error("Address→RC resolution failed (non-critical): %s", e)
        summary["errors"].append(f"rc_resolve: {e}")

    # Step 7b2b: Office-contact backfill — fill the handling office (notaría for
    # BOE-N, court for BOE-TEJU/V.B), its phone/email, the procedure number and any
    # named parties straight from the structured edict text. Deterministic (regex,
    # no LLM), idempotent, and free. THIS is the real contact path for inheritance
    # leads; citizen-heir web search (Step 7b3) is ~0% in Spain.
    try:
        from nadia_ai.utils.edict_parse import backfill_edict_contacts

        # limit caps a single pass (one BOE PDF fetch per lead); the budget guard
        # caps the cumulative time across enrichment steps.
        _backfill_limit = int(os.getenv("BOE_BACKFILL_MAX_PER_RUN", "150"))
        office_stats = (
            backfill_edict_contacts(conn, limit=_backfill_limit)
            if _budget_ok("office-contact backfill")
            else {}
        )
        summary["office_contacts"] = office_stats
        logger.info("Office-contact backfill: %s", office_stats)
    except Exception as e:
        logger.error("Office-contact backfill failed (non-critical): %s", e)
        summary["errors"].append(f"office_backfill: {e}")

    # Step 7b2d: Heraldo heir backfill — fill named family (spouse/children) parsed
    # deterministically from the "Sus apenados" block of Heraldo de Aragón esquelas.
    # This is the highest-value LOCAL signal (a death notice that names the heirs).
    # merge already persists these at scrape time; this is the idempotent safety net
    # for any Heraldo lead created without them (e.g. via a cross-source merge).
    try:
        from nadia_ai.scrapers.heraldo import backfill_heraldo_heirs

        heraldo_heirs = backfill_heraldo_heirs(conn)
        summary["heraldo_heirs"] = heraldo_heirs
        logger.info("Heraldo heir backfill: %s", heraldo_heirs)
    except Exception as e:
        logger.error("Heraldo heir backfill failed (non-critical): %s", e)
        summary["errors"].append(f"heraldo_heirs: {e}")

    # Step 7b2c: Notaría phone resolution — fill the public phone of the notaría
    # handling each BOE-N declaración (name+city known from the edict, no phone in
    # the text). Unlike heir search this CONVERTS (~100%): notarías are public, so
    # this makes the warmest cohort callable. Citation+format guarded; only touches
    # leads missing a phone, so it's ~12 calls/day in steady state.
    try:
        from nadia_ai.config import RESOLVE_NOTARY_PHONE
        from nadia_ai.enrich_contact import resolve_office_phones

        if not RESOLVE_NOTARY_PHONE:
            # Disabled by default — the client wants the heir's contact, not the
            # notaría's. Skipping also stops the Perplexity 401 spam and frees the
            # budget for address/Catastro enrichment (the postal-mail path).
            logger.info("Notaría phone resolution disabled (RESOLVE_NOTARY_PHONE=0)")
            office_phones = 0
        else:
            office_phones = resolve_office_phones(conn) if _budget_ok("notaría phone") else 0
        summary["office_phones"] = office_phones
        logger.info("Notaría phone resolution: %d leads got a phone", office_phones)
    except Exception as e:
        logger.error("Office-phone resolution failed (non-critical): %s", e)
        summary["errors"].append(f"office_phones: {e}")

    # Step 7b2d: Heir phone via RocketReach (opt-in: RESOLVE_HEIR_PHONE=1 +
    # ROCKETREACH_API_KEY). Tries to find a named heir's phone. Low yield for private
    # heirs + wrong-person risk → stored as low confidence. No-op when disabled.
    try:
        from nadia_ai.enrich_rocketreach import resolve_heir_phones

        heir_phones = resolve_heir_phones(conn) if _budget_ok("heir phone (RocketReach)") else 0
        summary["heir_phones"] = heir_phones
        logger.info("RocketReach heir-phone: %d leads got a phone", heir_phones)
    except Exception as e:
        logger.error("Heir-phone resolution failed (non-critical): %s", e)
        summary["errors"].append(f"heir_phones: {e}")

    # Step 7b3: Contact discovery — search-native LLM (Perplexity Sonar) turns an
    # heir/causante name + city into a personal contact path. OFF by default
    # (CONTACT_ENRICH_MAX_PER_RUN=0): measured ~0% for citizen heirs in Spain, so it
    # only burns budget. Left wired for a real skip-trace provider or a manual pass.
    try:
        from nadia_ai.enrich_contact import run_contact_enrichment

        contacts = run_contact_enrichment(conn) if _budget_ok("contact discovery") else 0
        summary["contacts_found"] = contacts
        logger.info("Contact enrichment: %d leads got a contact path", contacts)
    except Exception as e:
        logger.error("Contact enrichment failed (non-critical): %s", e)
        summary["errors"].append(f"contact_enrichment: {e}")

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
        # Network fetches hit subastas.boe.es (often IP-blocked from CI, 60s
        # timeouts) — skip them once the budget is spent, but still cross-join any
        # data already cached so delivery is never delayed by this step.
        if _budget_ok("subastas/obras fetch"):
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

        # Per-comunidad actionability scorecard (whole DB): the "where can we already
        # commercialize?" funnel — total/TierA + %heir/%address/%RC/%ready per CCAA,
        # logged every run as standing market intel. Never break the run over it.
        try:
            from nadia_ai.scorecard import log_scorecard

            log_scorecard(conn)
        except Exception as e:
            logger.warning("Scorecard failed (non-critical): %s", e)

        deliver(leads, summary)
    except Exception as e:
        logger.error("Delivery failed: %s", e)
        summary["errors"].append(f"delivery: {e}")

    # Step 8b: Refresh the agent-facing actionable call-list (exports/*.xlsx).
    # The dashboard is the primary delivery; this is the offline worklist the agent
    # opens — ranked by open legal-deadline + action class. Never break the run.
    if isinstance(conn, sqlite3.Connection):
        try:
            from tools.export_call_list import build

            xlsx = build()
            summary["call_list"] = xlsx
            logger.info("Actionable call-list written to %s", xlsx)

            # Email the worklist to Nadia as an attachment (skips silently if
            # SMTP/recipient not configured). The dashboard is the live view;
            # this is the zero-setup copy in her inbox each morning.
            try:
                from nadia_ai.delivery import email_worklist

                if email_worklist(xlsx, summary):
                    summary["worklist_emailed"] = True
            except Exception as e:
                logger.error("Worklist email failed (non-critical): %s", e)
                summary["errors"].append(f"worklist_email: {e}")
        except Exception as e:
            logger.error("Call-list export failed (non-critical): %s", e)
            summary["errors"].append(f"call_list: {e}")

    # Step 8c: FSBO ("venta por particular") feed — scrape pisos.com ONCE and write
    # the FSBO call-list, then hand the same listings to the outreach pack (8d) so
    # the owner-phone scrape isn't repeated. Standalone product (NOT merged into the
    # inheritance DB); never break the run.
    fsbo_leads: list = []
    if isinstance(conn, sqlite3.Connection):
        try:
            from nadia_ai.config import FSBO_LOCALIDADES
            from nadia_ai.scrapers.fsbo import scrape_fsbo
            from tools.fsbo_export import build as build_fsbo

            fsbo_leads = scrape_fsbo(localidades=FSBO_LOCALIDADES, max_per_loc=60)
            fsbo_xlsx = build_fsbo(leads=fsbo_leads)
            summary["fsbo_listings"] = len(fsbo_leads)
            summary["fsbo_with_phone"] = sum(1 for x in fsbo_leads if x.get("phone"))
            logger.info("FSBO feed: %d listings (%d w/ phone) -> %s",
                        len(fsbo_leads), summary["fsbo_with_phone"], fsbo_xlsx)
        except Exception as e:
            logger.error("FSBO feed failed (non-critical): %s", e)
            summary["errors"].append(f"fsbo: {e}")

    # Step 8d: Outreach pack — per-lead, channel-appropriate Spanish copy (FSBO call
    # scripts + inheritance/obituary letters). Deterministic by default (free, no
    # LLM); reuses the Step-8c FSBO listings. The agent's ready-to-send morning pack.
    if isinstance(conn, sqlite3.Connection):
        try:
            from tools.generate_outreach import build as build_outreach

            outreach_xlsx = build_outreach(fsbo_leads=fsbo_leads, use_llm=False)
            summary["outreach_export"] = outreach_xlsx
            logger.info("Outreach pack -> %s", outreach_xlsx)
        except Exception as e:
            logger.error("Outreach pack failed (non-critical): %s", e)
            summary["errors"].append(f"outreach: {e}")

    elapsed = time.monotonic() - start
    summary["elapsed_seconds"] = round(elapsed, 2)
    logger.info(
        "Pipeline finished in %.2fs — %d leads, %d errors",
        elapsed,
        summary["leads_today"],
        len(summary["errors"]),
    )

    # Persist the summary so per-scraper counts and errors survive the run.
    # Scrapers that silently yield 0 records are invisible otherwise.
    try:
        import json
        from pathlib import Path

        logs_dir = Path("logs")
        logs_dir.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y-%m-%d_%H%M%S")
        summary_path = logs_dir / f"run_summary_{stamp}.json"
        summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        logger.info("Run summary written to %s", summary_path)
        zero_scrapers = [
            k.removesuffix("_new") for k, v in summary.items() if k.endswith("_new") and v == 0
        ]
        if zero_scrapers:
            logger.warning("Scrapers with ZERO records this run: %s", ", ".join(zero_scrapers))
    except Exception as e:
        logger.error("Failed to write run summary: %s", e)

    # Append a cumulative-metrics snapshot + refresh docs/METRICS.md so engine
    # health is tracked over time (never let this break the pipeline). Guard on a
    # real sqlite connection so a mocked conn under test can't pollute the file.
    if isinstance(conn, sqlite3.Connection):
        try:
            from tools.metrics_snapshot import append_snapshot, render_doc, snapshot

            append_snapshot(snapshot(conn), label="daily run")
            render_doc()
            logger.info("Metrics snapshot appended (docs/METRICS.md)")
        except Exception as e:
            logger.error("Metrics snapshot failed (non-critical): %s", e)

    conn.close()
    return summary


if __name__ == "__main__":
    run_pipeline()
