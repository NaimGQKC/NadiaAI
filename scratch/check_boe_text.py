import sqlite3
import json
import requests
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nadia_ai.scrapers.boe import extract_pdf_text

conn = sqlite3.connect('nadia_ai.db')
conn.row_factory = sqlite3.Row

# Get a few BOE leads
leads = conn.execute("SELECT id, causante, source_urls FROM leads WHERE sources LIKE '%BOE%' AND ai_extraction_done = 1").fetchall()

for r in leads[:3]:
    urls = json.loads(r['source_urls'])
    if urls:
        url = urls[0]
        print(f"Lead ID: {r['id']}, URL: {url}")
        print("Extracting PDF text...")
        text = extract_pdf_text(url)
        print("=== TEXT START ===")
        print(text[:1000])
        print("=== TEXT END ===")
        print("=" * 80)

conn.close()
