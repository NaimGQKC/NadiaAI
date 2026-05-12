"""Run heir extraction in a loop until all leads are processed."""
import logging
from nadia_ai.db import get_connection
from nadia_ai.utils.extraction import run_heir_extraction

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("nadia_ai.extraction_loop")

def main():
    conn = get_connection()
    total_enriched = 0
    while True:
        logger.info("Starting extraction batch...")
        count = run_heir_extraction(conn)
        if count == 0:
            logger.info("No more leads to process.")
            break
        total_enriched += count
        logger.info("Enriched %d leads in this batch. Total so far: %d", count, total_enriched)
    
    conn.close()
    logger.info("Extraction loop finished. Total enriched: %d", total_enriched)

if __name__ == "__main__":
    main()
