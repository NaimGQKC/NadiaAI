import logging
import sqlite3
import json
from datetime import UTC, datetime, timedelta
from nadia_ai.db import get_connection, init_db
from nadia_ai.scrapers.boe import scrape_boe
from nadia_ai.merge import merge_leads
from nadia_ai.utils.extraction import run_heir_extraction

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger("sample_pipeline")

def run_sample():
    logger.info("Starting SAMPLE Full Pipeline (50 leads)...")
    conn = get_connection()
    init_db(conn)

    # 1. Scrape a small bit of BOE (last 2 days) to show current data ingestion
    logger.info("Step 1: Scraping recent BOE edicts...")
    boe_records = scrape_boe(days=2)
    logger.info(f"Found {len(boe_records)} recent BOE records.")

    # 2. Merge them into leads
    logger.info("Step 2: Merging records into lead database...")
    merge_stats = merge_leads(conn, boe_records)
    logger.info(f"Merge stats: {merge_stats}")

    # 3. Reset 50 leads for extraction (prioritize Tier A)
    logger.info("Step 3: Resetting 50 leads for high-quality LLM extraction...")
    conn.execute("""
        UPDATE leads 
        SET ai_extraction_done = 0 
        WHERE id IN (
            SELECT id FROM leads 
            WHERE (heir_names_json IS NULL OR heir_names_json = '[]')
            ORDER BY tier ASC, first_seen_at DESC 
            LIMIT 50
        )
    """)
    conn.commit()

    # 4. Run the LLM Extraction
    logger.info("Step 4: Running LLM extraction on sample leads...")
    # I'll temporarily patch run_heir_extraction limit to 50 for this run
    count = run_heir_extraction(conn)
    logger.info(f"Enriched {count} leads with heirs and addresses.")

    # 5. Final check of results
    rows = conn.execute("""
        SELECT id, causante, heir_name, direccion, tier 
        FROM leads 
        WHERE ai_extraction_done = 1 
        ORDER BY last_updated_at DESC 
        LIMIT 10
    """).fetchall()
    
    print("\n--- SAMPLE RESULTS (Top 10) ---")
    for r in rows:
        print(f"ID: {r['id']} | Tier: {r['tier']} | Causante: {r['causante']} | Heir: {r['heir_name']} | Addr: {r['direccion']}")
    
    conn.close()
    logger.info("Sample pipeline finished.")

if __name__ == "__main__":
    run_sample()
