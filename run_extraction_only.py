import sqlite3
import logging
from nadia_ai.utils.extraction import run_heir_extraction
from nadia_ai.db import get_connection

logging.basicConfig(level=logging.INFO)

def main():
    conn = get_connection()
    from nadia_ai.merge import init_leads_schema
    init_leads_schema(conn)
    count = run_heir_extraction(conn)
    print(f"Enriched {count} leads")
    conn.close()

if __name__ == "__main__":
    main()
