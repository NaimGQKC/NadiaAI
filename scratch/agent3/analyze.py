import sqlite3, json, re
c = sqlite3.connect("scratch/agent3/test.db")
c.row_factory = sqlite3.Row
def n(sql, args=()):
    return c.execute(sql, args).fetchone()[0]

total = n("SELECT COUNT(*) FROM leads")
print("TOTAL leads:", total)
print("ref_catastral non-empty:", n("SELECT COUNT(*) FROM leads WHERE ref_catastral IS NOT NULL AND ref_catastral != ''"))
print("referencia_catastral non-empty:", n("SELECT COUNT(*) FROM leads WHERE referencia_catastral IS NOT NULL AND referencia_catastral != ''"))
print("heir_names_json populated:", n("SELECT COUNT(*) FROM leads WHERE heir_names_json NOT IN ('[]','','[\"\"]') AND heir_names_json IS NOT NULL"))
print("direccion non-empty:", n("SELECT COUNT(*) FROM leads WHERE direccion IS NOT NULL AND direccion != ''"))
print("address_norm non-empty:", n("SELECT COUNT(*) FROM leads WHERE address_norm IS NOT NULL AND address_norm != ''"))
print("localidad non-empty:", n("SELECT COUNT(*) FROM leads WHERE localidad IS NOT NULL AND localidad != ''"))
print("direccion with a digit (street#):", n("SELECT COUNT(*) FROM leads WHERE direccion GLOB '*[0-9]*'"))
print("ai_extraction_done=1:", n("SELECT COUNT(*) FROM leads WHERE ai_extraction_done=1"))
print("ai_extraction_done=0:", n("SELECT COUNT(*) FROM leads WHERE ai_extraction_done=0"))

print("\n--- sources breakdown ---")
from collections import Counter
cnt = Counter()
for (s,) in c.execute("SELECT sources FROM leads"):
    try:
        for src in json.loads(s or "[]"):
            cnt[src]+=1
    except: pass
for k,v in cnt.most_common():
    print(f"  {k}: {v}")

print("\n--- sample direccion values (non-empty) ---")
for (d,) in c.execute("SELECT direccion FROM leads WHERE direccion != '' LIMIT 25"):
    print("  |", d[:90])

print("\n--- enrichment tables ---")
print("enrichment_obras rows:", n("SELECT COUNT(*) FROM enrichment_obras"))
print("  obras w/ address_norm:", n("SELECT COUNT(*) FROM enrichment_obras WHERE address_norm IS NOT NULL AND address_norm!=''"))
print("  obras w/ RC:", n("SELECT COUNT(*) FROM enrichment_obras WHERE referencia_catastral IS NOT NULL AND referencia_catastral!=''"))
print("enrichment_subastas rows:", n("SELECT COUNT(*) FROM enrichment_subastas"))

print("\n--- sample obras address_norm ---")
for r in c.execute("SELECT direccion, address_norm FROM enrichment_obras WHERE address_norm!='' LIMIT 15"):
    print("  obra:", (r[0] or "")[:50], "=> NORM:", r[1])

print("\n--- parcels table ---")
print("parcels rows:", n("SELECT COUNT(*) FROM parcels"))
print("parcels enriched (not pending):", n("SELECT COUNT(*) FROM parcels WHERE enrichment_pending=0"))
