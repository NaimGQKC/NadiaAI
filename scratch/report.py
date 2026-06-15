import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import sqlite3
from nadia_ai.config import DB_PATH

c = sqlite3.connect(DB_PATH); c.row_factory = sqlite3.Row
def n(w): return c.execute("SELECT COUNT(*) k FROM leads WHERE " + w).fetchone()["k"]

print("=== 1. SCALE & TIER MIX ===")
print("total leads:", n("1=1"))
for t in ("A", "B", "C", "X"):
    print("  tier %s: %d" % (t, n("tier='%s'" % t)))
print("named heir (actionable core):", n("COALESCE(heir_name,'')!=''"))
print("named heir + address:", n("COALESCE(heir_name,'')!='' AND COALESCE(direccion,'')!=''"))
print("multi-heir lists:", n("heir_names_json LIKE '%,%'"))

print("\n=== 2. SOURCE MIX (volume + heir yield) ===")
print("  %-22s %6s %6s %6s" % ("source", "leads", "heirs", "yield%"))
for r in c.execute(
    "SELECT sources, COUNT(*) t, SUM(CASE WHEN COALESCE(heir_name,'')!='' THEN 1 ELSE 0 END) h "
    "FROM leads GROUP BY sources ORDER BY t DESC LIMIT 14"):
    y = 100.0*(r["h"] or 0)/r["t"] if r["t"] else 0
    print("  %-22s %6d %6d %5.0f%%" % (str(r["sources"])[:22], r["t"], r["h"] or 0, y))

print("\n=== 3. GEOGRAPHY: Zaragoza vs Spain ===")
zgz = "(region='Zaragoza' OR localidad LIKE '%aragoza%')"
print("Zaragoza leads:", n(zgz), "| named heir:", n(zgz+" AND COALESCE(heir_name,'')!=''"),
      "| tier A:", n(zgz+" AND tier='A'"))
print("Rest of Spain:", n("NOT("+zgz+")"), "| named heir:", n("NOT("+zgz+") AND COALESCE(heir_name,'')!=''"))
print("Zaragoza share of named-heir leads: %.0f%%" %
      (100.0*n(zgz+" AND COALESCE(heir_name,'')!=''")/max(n("COALESCE(heir_name,'')!=''"),1)))

print("\n=== 4. NOTARIAL ENGINE (Sección V) ===")
print("notarial leads:", n("sources LIKE '%Notarial%'"),
      "| with heir:", n("sources LIKE '%Notarial%' AND COALESCE(heir_name,'')!=''"),
      "| multi-heir:", n("sources LIKE '%Notarial%' AND heir_names_json LIKE '%,%'"))

print("\n=== 5. CONTACT / OUTREACH ===")
print("contact searched:", n("COALESCE(contact_enriched_at,'')!=''"),
      "| contact found:", n("(COALESCE(contact_phone,'')!='' OR COALESCE(contact_email,'')!='' OR COALESCE(contact_profile_url,'')!='')"))
print("outreach-eligible (A/B, allowed, named heir):",
      n("tier IN ('A','B') AND outreach_allowed=1 AND COALESCE(heir_name,'')!=''"))
