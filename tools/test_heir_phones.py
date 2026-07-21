"""Empirical heir-phone COVERAGE test against the real heirs in the DB.

Runs on PEDRO (where the persistent DB lives). For a sample of real heirs it calls a
people-enrichment provider and reports how many the provider even HAS a phone for.

Why this works on a FREE key: People Data Labs returns PII fields as a boolean `true`
when your plan doesn't include the value (e.g. "phone_numbers": true means "we have a
number but upgrade to see it"). So even without paying we can measure the CEILING —
the fraction of our real heirs the provider could return a number for on a paid plan.
That is the go/no-go signal for whether paying is worth it.

Usage (on PEDRO, PowerShell):
    $env:HEIR_TEST_KEY="<api-key>"; $env:HEIR_TEST_SAMPLE="50"; python tools/test_heir_phones.py

Read-only: never writes to the DB. Provider defaults to PDL (pdl); proxycurl stub
included for when that key is available.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time

import requests

import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from nadia_ai.config import DB_PATH  # noqa: E402

try:
    from nadia_ai.utils.regions import ccaa_for  # noqa: E402
except Exception:  # pragma: no cover - fallback if module path differs
    ccaa_for = None

# HEIR_TEST_PROVIDER carries colon-separated flags so we can scope a run without any
# workflow change (reuses the provider input). Examples:
#   "pdl:aragon"      -> region filter Aragón
#   "pdl:aragon:a"    -> region Aragón + Tier A only
#   "pdl:aragon:a:dry"-> same, but DRY RUN (list selected heirs, 0 credits, no writes)
_segs = os.getenv("HEIR_TEST_PROVIDER", "pdl").lower().split(":")
PROVIDER = _segs[0]
DRY_RUN = "dry" in _segs[1:]
TIER_FILTER = next((s.upper() for s in _segs[1:] if s in ("a", "b", "c")), "")
CCAA_FILTER = next((s for s in _segs[1:] if s not in ("dry", "a", "b", "c")), "")
API_KEY = os.getenv("HEIR_TEST_KEY", "")
SAMPLE = int(os.getenv("HEIR_TEST_SAMPLE", "50"))
COUNTRY = os.getenv("HEIR_TEST_COUNTRY", "Spain")

_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
# Aragón provinces — used to filter/detect region without relying only on ccaa_for.
_ARAGON = {"zaragoza", "huesca", "teruel", "aragon"}
# Name connectors/honorifics that don't count as a real name token.
_STOP = {"de", "del", "la", "el", "los", "las", "y", "e", "san", "santa",
         "don", "dona", "sr", "sra", "d", "da", "vda", "viuda"}
# Edict boilerplate that leaks into heir_name when extraction is noisy — never enrich these.
_BOILERPLATE = {"pudiendo", "acompanados", "acompanado", "herederos", "heredero",
                "herencia", "yacente", "ignorados", "desconocidos", "causante",
                "finca", "abintestato", "interesados", "cuantos", "demas", "otros"}
# Institutions/entities that get named as "heir" (e.g. the State when there are no
# relatives) — never a real person.
_ENTITY = {"comunidad", "autonoma", "ayuntamiento", "diputacion", "generalitat",
           "gobierno", "junta", "consejo", "ministerio", "tesoreria", "agencia",
           "hacienda", "estado", "fundacion", "asociacion", "sociedad", "cooperativa",
           "banco", "caja", "iglesia", "parroquia", "universidad", "instituto",
           "colegio", "hospital", "generalidad", "consorcio", "patronato"}
# Common Spanish given names — used to reject 2-token names that are a compound FIRST
# name with no surname captured ("Juan José", "María Pilar").
_GIVEN = {
    "juan", "jose", "maria", "pilar", "carmen", "ana", "luis", "pedro", "angel",
    "jesus", "antonio", "francisco", "manuel", "javier", "miguel", "jorge", "carlos",
    "david", "daniel", "pablo", "sergio", "alberto", "alejandro", "fernando", "ramon",
    "rafael", "vicente", "ignacio", "andres", "alfonso", "enrique", "emilio", "tomas",
    "ruben", "oscar", "mario", "adrian", "marcos", "ivan", "victor", "diego", "raul",
    "isabel", "dolores", "josefa", "teresa", "rosa", "francisca", "antonia", "cristina",
    "laura", "marta", "elena", "sara", "paula", "lucia", "sofia", "nuria", "raquel",
    "beatriz", "rocio", "montserrat", "silvia", "patricia", "susana", "monica",
    "angeles", "mercedes", "concepcion", "manuela", "encarnacion", "julia", "eva",
    "irene", "alba", "clara", "juana", "amparo", "consuelo", "gloria", "esther",
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return s.strip().lower()


def _good_heir_name(name: str) -> bool:
    """True only for a demo-quality real full name. Rejects: digits, edict boilerplate,
    institutions, initials/truncations, and compound-first-name-only (no surname)."""
    if not name or any(ch.isdigit() for ch in str(name)):
        return False
    toks = [t for t in re.split(r"[^a-z]+", _norm(name)) if t]
    if any(t in _BOILERPLATE or t in _ENTITY for t in toks):
        return False
    core = [t for t in toks if t not in _STOP and len(t) >= 3]
    if len(core) < 2:                       # need first name + a real surname token
        return False
    if all(t in _GIVEN for t in core):      # e.g. "Juan José" / "María Pilar" — no surname
        return False
    # Accent-corruption guard: real name tokens are capitalized; a stripped accent
    # leaves a lowercase-initial fragment ("ngel" <- "Ángel"). Reject those.
    for tk in re.split(r"\s+", str(name).strip()):
        low = _norm(tk)
        if not low or low in _STOP:
            continue
        first_alpha = next((c for c in tk if c.isalpha()), "")
        if first_alpha and first_alpha.islower():
            return False
    return True


def _in_ccaa(region: str, localidad: str, target: str) -> bool:
    """True if this lead belongs to the target CCAA (accent-insensitive)."""
    if not target:
        return True
    t = _norm(target)
    if t in ("aragon", "aragón"):
        blob = f"{_norm(region)} {_norm(localidad)}"
        if any(p in blob.split() or p in blob for p in _ARAGON):
            return True
    if ccaa_for is not None:
        try:
            return _norm(ccaa_for(region, localidad)) == t
        except Exception:
            return False
    return False


def _split_name(full: str) -> tuple[str, str]:
    parts = [p for p in str(full or "").split() if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _classify_field(data: dict, keys: tuple[str, ...], pattern: re.Pattern) -> str:
    """Classify a contact field across candidate keys → value|flag|none.
      value = an actual usable string is present (free-tier win!)
      flag  = provider HAS it but masks the value to a boolean `true` (paid PII)
      none  = provider has nothing (False / absent / 404)."""
    if not isinstance(data, dict):
        return "none"
    saw_flag = False
    for key in keys:
        v = data.get(key)
        if isinstance(v, list) and v:
            if any(isinstance(x, str) and pattern.search(x) for x in v):
                return "value"
            saw_flag = True
        elif isinstance(v, str) and pattern.search(v):
            return "value"
        elif v is True:
            saw_flag = True
    return "flag" if saw_flag else "none"


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_PHONE_KEYS = ("phone_numbers", "mobile_phone", "phone")
_EMAIL_KEYS = ("emails", "personal_emails", "recommended_personal_email", "work_email")


def _extract_value(data: dict, keys: tuple[str, ...], pattern: re.Pattern) -> str:
    """Return the first actual usable string across candidate keys (paid key), else ''."""
    if not isinstance(data, dict):
        return ""
    for key in keys:
        v = data.get(key)
        if isinstance(v, list):
            for x in v:
                if isinstance(x, str) and pattern.search(x):
                    return x
                if isinstance(x, dict):
                    for kk in ("address", "email", "value", "number"):
                        if isinstance(x.get(kk), str) and pattern.search(x[kk]):
                            return x[kk]
        elif isinstance(v, str) and pattern.search(v):
            return v
    return ""


def _lookup_pdl(name: str, locality: str, region: str, street: str = "") -> dict:
    """Returns a dict with phone/email class, actual email/phone VALUES (paid key),
    linkedin, matched_name, likelihood, and whether the call was a billable match."""
    fname, lname = _split_name(name)
    params = {
        "first_name": fname,
        "last_name": lname,
        "country": COUNTRY,
        "min_likelihood": 2,
    }
    if locality:
        params["locality"] = locality
    if region:
        params["region"] = region
    if street:
        params["street_address"] = street  # disambiguates same-name people
    headers = {"X-Api-Key": API_KEY}
    for attempt in range(3):
        try:
            r = requests.get(
                "https://api.peopledatalabs.com/v5/person/enrich",
                params=params, headers=headers, timeout=25,
            )
            if r.status_code == 200:
                body = r.json()
                data = body.get("data", {}) or {}
                return {
                    "phone": _classify_field(data, _PHONE_KEYS, _PHONE_RE),
                    "email": _classify_field(data, _EMAIL_KEYS, _EMAIL_RE),
                    "email_value": _extract_value(data, _EMAIL_KEYS, _EMAIL_RE),
                    "phone_value": _extract_value(data, _PHONE_KEYS, _PHONE_RE),
                    "linkedin": data.get("linkedin_url") or "",
                    "matched": (data.get("full_name") or "")[:28],
                    "lk": body.get("likelihood", 0),
                    "billable": True,  # a 200 match consumes 1 PDL credit
                }
            if r.status_code == 404:
                return {"phone": "none", "email": "none", "email_value": "", "phone_value": "",
                        "linkedin": "", "matched": "", "lk": 0, "billable": False}
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            return {"phone": f"http_{r.status_code}", "email": "none", "email_value": "",
                    "phone_value": "", "linkedin": "", "matched": "", "lk": 0, "billable": False}
        except (requests.RequestException, ValueError):
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            return {"phone": "error", "email": "none", "email_value": "", "phone_value": "",
                    "linkedin": "", "matched": "", "lk": 0, "billable": False}
    return {"phone": "none", "email": "none", "email_value": "", "phone_value": "",
            "linkedin": "", "matched": "", "lk": 0, "billable": False}


def _ensure_email_column(conn: sqlite3.Connection) -> None:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(leads)")]
    if "contact_email" not in cols:
        conn.execute("ALTER TABLE leads ADD COLUMN contact_email TEXT")
        conn.commit()


def main() -> int:
    # Windows consoles default to cp1252 and choke on non-ASCII names/box chars.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if not API_KEY:
        print("ERROR: set HEIR_TEST_KEY", file=sys.stderr)
        return 2
    if PROVIDER != "pdl":
        print(f"ERROR: provider {PROVIDER!r} not implemented yet (only 'pdl')", file=sys.stderr)
        return 2

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_email_column(conn)
    # Fetch a broad candidate set (address-first, no email yet), then filter by tier /
    # CCAA / name-quality in Python so credits are only ever spent on genuine target
    # heirs. Only PDL calls cost — this DB scan is free.
    tier_sql = "tier = ?" if TIER_FILTER else "tier IN ('A','B')"
    params = (TIER_FILTER,) if TIER_FILTER else ()
    candidates = conn.execute(
        f"""
        SELECT id, heir_name, localidad, region, direccion, tier FROM leads
        WHERE heir_name IS NOT NULL AND heir_name != ''
          AND {tier_sql}
          AND (contact_email IS NULL OR contact_email = '')
        ORDER BY (CASE WHEN direccion IS NOT NULL AND direccion != '' THEN 0 ELSE 1 END),
                 first_seen_at DESC
        """,
        params,
    ).fetchall()

    # Region + name-quality filters (heir_name is the HEIR, never causante/notary).
    rows = [
        r for r in candidates
        if _in_ccaa(r["region"], r["localidad"], CCAA_FILTER) and _good_heir_name(r["heir_name"])
    ][:SAMPLE]

    if not rows:
        scope = f" (tier={TIER_FILTER or 'A/B'}, ccaa={CCAA_FILTER or 'all'})"
        print(f"No qualifying heirs in DB{scope}.")
        return 0
    print(f"[filters: tier={TIER_FILTER or 'A/B'} | ccaa={CCAA_FILTER or 'all'} | good-name -> "
          f"{len(rows)} heirs selected (of {len(candidates)} candidates)]")

    if DRY_RUN:
        print("\n=== DRY RUN — heirs that WOULD be queried (0 credits, no PDL calls) ===")
        for i, row in enumerate(rows, 1):
            print(f"{i:>3}. [{row['tier']}] {row['heir_name'][:38]:38} | "
                  f"{(row['localidad'] or '')[:16]:16} | {(row['direccion'] or '')[:34]}")
        print(f"\nTotal: {len(rows)} heirs. Re-run without ':dry' to enrich (~{len(rows)} credits max).")
        return 0

    max_matches = int(os.getenv("HEIR_MAX_MATCHES", "330"))  # credit safety cap (<350)

    print(f"=== Heir contact enrichment | provider={PROVIDER} | sample={len(rows)} | country={COUNTRY} ===")
    print(f"    credit cap: stop after {max_matches} matches | writes real emails to DB when found\n")
    ph = {"value": 0, "flag": 0, "none": 0, "other": 0}
    em = {"value": 0, "flag": 0, "none": 0, "other": 0}
    matched = 0
    written = 0
    found_emails: list[tuple] = []
    for i, row in enumerate(rows, 1):
        if matched >= max_matches:
            print(f"\n[credit cap reached: {matched} matches ~= credits — stopping]")
            break
        name = row["heir_name"]
        loc = (row["localidad"] or "").strip()
        reg = (row["region"] or "").strip()
        street = (row["direccion"] or "").strip()
        r = _lookup_pdl(name, loc, reg, street)
        ph[r["phone"] if r["phone"] in ph else "other"] += 1
        em[r["email"] if r["email"] in em else "other"] += 1
        if r["billable"]:
            matched += 1
        # Write any REAL email value (paid key) to the DB — low confidence, keep LinkedIn for verify.
        if r["email_value"]:
            conn.execute(
                """UPDATE leads SET
                     contact_email = ?,
                     contact_source = 'pdl',
                     contact_confidence = 'heir-pdl (low)',
                     contact_enriched_at = datetime('now'),
                     last_updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (r["email_value"], row["id"]),
            )
            conn.commit()
            written += 1
            found_emails.append((name, r["email_value"], r["linkedin"], r["matched"]))
        print(f"{i:>3}. {name[:28]:28} | {loc[:12]:12} | lk={r['lk']} | em:{r['email']:5} "
              f"| {r['email_value'][:30]:30} | -> {r['matched']}")

    n = i
    em_ceiling = em["value"] + em["flag"]
    pct = lambda x: f"{100*x//n if n else 0}%"
    print("\n=== SUMMARY ===")
    print(f"  heirs processed:                {n}")
    print(f"  billable matches (credits used):{matched}")
    print(f"  person matched at all:          {matched}/{n}  ({pct(matched)})")
    print(f"  EMAIL ceiling (has an email):   {em_ceiling}/{n}  ({pct(em_ceiling)})")
    print(f"  >>> REAL EMAILS WRITTEN TO DB:  {written}/{n}  ({pct(written)}) <<<")
    if found_emails:
        print("\n=== EMAILS FOUND (spot-check identity via matched name / LinkedIn) ===")
        for nm, mail, li, mtc in found_emails:
            print(f"  {nm[:34]:34} -> {mail}  | match={mtc} | {li}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
