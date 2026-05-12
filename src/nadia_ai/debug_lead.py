import sqlite3
import json
import logging
from nadia_ai.utils.extraction import extract_inheritance_data
from nadia_ai.scrapers.boe import extract_pdf_text

logging.basicConfig(level=logging.INFO)

def debug():
    conn = sqlite3.connect('nadia_ai.db')
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM leads WHERE causante LIKE '%Mederos%' LIMIT 1").fetchone()
    if not row:
        print("Lead not found.")
        return
    
    print(f"Lead ID: {row['id']} | Causante: {row['causante']}")
    urls = json.loads(row['source_urls'])
    print(f"URLs: {urls}")
    
    context = ""
    for url in urls:
        print(f"Fetching {url}...")
        text = extract_pdf_text(url)
        if text:
            print(f"Extracted {len(text)} chars.")
            context += text + "\n"
    
    if not context:
        print("No text extracted.")
        return
        
    print("Running LLM extraction...")
    result = extract_inheritance_data(context, row['causante'])
    print(f"Result: {result}")
    
    conn.close()

if __name__ == "__main__":
    debug()
