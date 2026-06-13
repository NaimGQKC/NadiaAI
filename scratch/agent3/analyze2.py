import sqlite3, json
c = sqlite3.connect("scratch/agent3/test.db")
c.row_factory = sqlite3.Row

print("--- obras sample raw ---")
for r in c.execute("SELECT expediente, referencia_catastral, direccion, address_norm, tipo_licencia FROM enrichment_obras LIMIT 10"):
    print(dict(r))

print("\n--- obras: how many have a non-empty direccion? ---")
print(c.execute("SELECT COUNT(*) FROM enrichment_obras WHERE direccion IS NOT NULL AND direccion!=''").fetchone()[0])

print("\n--- RC format in obras (claveCatastro)? sample ---")
for (rc,) in c.execute("SELECT referencia_catastral FROM enrichment_obras LIMIT 10"):
    print("  RC:", repr(rc), "len:", len(rc) if rc else 0)

print("\n--- Which sources have street-level direccion? Join leads.direccion to sources ---")
from collections import Counter
src_with_dir = Counter()
for r in c.execute("SELECT sources, direccion FROM leads WHERE direccion!=''"):
    try:
        for s in json.loads(r[0] or "[]"):
            src_with_dir[s]+=1
    except: pass
for k,v in src_with_dir.most_common():
    print(f"  {k}: {v} leads w/ direccion")

print("\n--- Leads with heirs: which sources? ---")
src_with_heirs = Counter()
for r in c.execute("SELECT sources, heir_names_json FROM leads WHERE heir_names_json NOT IN ('[]','')"):
    try:
        for s in json.loads(r[0] or "[]"):
            src_with_heirs[s]+=1
    except: pass
for k,v in src_with_heirs.most_common():
    print(f"  {k}: {v}")

print("\n--- BOE TEJU/Notarial leads: extraction done? heirs? ---")
print("BOE TEJU total:", c.execute("SELECT COUNT(*) FROM leads WHERE sources LIKE '%TEJU%'").fetchone()[0])
print("BOE TEJU extraction_done:", c.execute("SELECT COUNT(*) FROM leads WHERE sources LIKE '%TEJU%' AND ai_extraction_done=1").fetchone()[0])
print("BOE TEJU w/ heirs:", c.execute("SELECT COUNT(*) FROM leads WHERE sources LIKE '%TEJU%' AND heir_names_json NOT IN ('[]','')").fetchone()[0])
print("BOE Notarial total:", c.execute("SELECT COUNT(*) FROM leads WHERE sources LIKE '%Notarial%'").fetchone()[0])
print("BOE Notarial extraction_done:", c.execute("SELECT COUNT(*) FROM leads WHERE sources LIKE '%Notarial%' AND ai_extraction_done=1").fetchone()[0])
print("BOE Notarial w/ heirs:", c.execute("SELECT COUNT(*) FROM leads WHERE sources LIKE '%Notarial%' AND heir_names_json NOT IN ('[]','')").fetchone()[0])

print("\n--- source_urls present for BOE leads? ---")
print("BOE leads w/ source_urls:", c.execute("SELECT COUNT(*) FROM leads WHERE sources LIKE '%BOE%' AND source_urls NOT IN ('[]','')").fetchone()[0])
for (u,) in c.execute("SELECT source_urls FROM leads WHERE sources LIKE '%TEJU%' AND source_urls NOT IN ('[]','') LIMIT 3"):
    print("  ", u[:120])
