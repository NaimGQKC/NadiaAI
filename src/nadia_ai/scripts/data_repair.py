import sqlite3
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("data_repair")

def repair():
    conn = sqlite3.connect('nadia_ai.db')
    cursor = conn.cursor()

    # 1. Purge Hallucinations
    hallucination_patterns = [
        '%Calle Principal%',
        '%Calle 123%',
        '%Direccion del Inmueble%',
        '%Nombre del Fallecido%',
        '%Calle de la Estacion%', # Sometimes used as placeholder
        '%Calle Mayor, 1%',
        '%None%'
    ]
    
    for pattern in hallucination_patterns:
        cursor.execute("DELETE FROM leads WHERE direccion LIKE ? OR causante LIKE ?", (pattern, pattern))
        logger.info(f"Deleted leads matching pattern: {pattern}")

    # 2. Fix BOE Links
    # Some links might be missing the domain or using wrong php script
    cursor.execute("SELECT id, source_urls FROM leads WHERE sources LIKE '%BOE%'")
    rows = cursor.fetchall()
    for lid, urls_json in rows:
        urls = json.loads(urls_json)
        fixed_urls = []
        for url in urls:
            if "boe.es" in url:
                # Ensure it's a permanent link if possible
                if "txt.php" in url or "xml.php" in url:
                    # Keep as is or prefer PDF
                    pass
                fixed_urls.append(url)
            else:
                fixed_urls.append(url)
        cursor.execute("UPDATE leads SET source_urls = ? WHERE id = ?", (json.dumps(fixed_urls), lid))

    # 3. Geography Normalization (Zaragoza -> Aragón)
    cursor.execute("UPDATE leads SET region = 'Aragón' WHERE localidad LIKE '%Zaragoza%' OR region LIKE '%Zaragoza%'")
    logger.info("Normalized Zaragoza leads to Aragón region")

    # 4. Classification (Residential vs Commercial)
    # We'll use a new column 'asset_type' or repurpose 'use_class'
    # For now, let's just make sure 'use_class' is set correctly for Traspasos
    cursor.execute("UPDATE leads SET use_class = 'Commercial' WHERE sources LIKE '%Traspasos%'")
    cursor.execute("UPDATE leads SET use_class = 'Residential' WHERE sources NOT LIKE '%Traspasos%' AND (use_class IS NULL OR use_class = '')")
    
    conn.commit()
    conn.close()
    logger.info("Data repair complete.")

if __name__ == "__main__":
    repair()
