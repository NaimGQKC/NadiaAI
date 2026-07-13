"""Heir-accuracy audit — answers "are we extracting the RIGHT heirs?".

Samples leads that have extracted heirs, re-reads each source document, and asks an
LLM judge to verify every extracted name is actually named as an heir / interested
party in the text (vs the deceased, the notary/court, or an invented name). Prints a
per-lead breakdown and an aggregate accuracy %.

Runs against the persistent DB (honours NADIA_DB_PATH) and needs EXTRACTION_API_KEY.
Read-only — never writes to the DB.

    python tools/audit_heirs.py            # audit 40 sampled leads
    python tools/audit_heirs.py 80         # audit 80
"""

import json
import sqlite3
import sys

DB = "nadia_ai.db"
try:
    from nadia_ai.config import DB_PATH
    DB = str(DB_PATH)
except Exception:
    pass

from nadia_ai.scrapers.boe import extract_pdf_text  # noqa: E402
from nadia_ai.utils.extraction import _extract_via_llm, _is_pdf_url  # noqa: E402

import requests  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

_JUDGE = """Eres un verificador jurídico. Te doy el texto de un edicto/anuncio de
herencia y una lista de NOMBRES que un sistema extrajo como "herederos". Para cada
nombre decide su papel REAL según el texto:
- "heredero": se nombra como heredero, hijo/a, viudo/a, legatario o parte interesada.
- "fallecido": es el causante/fallecido (NO un heredero).
- "oficina": es la notaría, juzgado, registro, banco o un profesional.
- "ausente": el nombre NO aparece en el texto.

Devuelve SOLO este JSON: {{"verdicts":[{{"name":"...","role":"heredero|fallecido|oficina|ausente"}}]}}

NOMBRES: {names}

TEXTO:
{text}"""


def _fetch_text(urls: list[str]) -> str:
    for url in urls:
        u = url + "/document" if "zaragoza.es/sede/servicio/tablon-edicto/" in url and not url.endswith("/document") else url
        try:
            if _is_pdf_url(u):
                t = extract_pdf_text(u)
            else:
                r = requests.get(u, timeout=15)
                t = BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True) if r.status_code == 200 else ""
            if t:
                return t
        except Exception:
            continue
    return ""


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT id, causante, heir_names_json, source_urls FROM leads
           WHERE heir_names_json NOT IN ('', '[]') AND heir_names_json IS NOT NULL
             AND source_urls LIKE '%http%'
           ORDER BY RANDOM() LIMIT ?""",
        (n,),
    ).fetchall()
    if not rows:
        print(f"No leads with heirs in {DB}.")
        return 0

    tot = good = 0
    role_counts = {"heredero": 0, "fallecido": 0, "oficina": 0, "ausente": 0}
    flagged = []
    audited = 0
    for r in rows:
        heirs = json.loads(r["heir_names_json"] or "[]")
        if not heirs:
            continue
        text = _fetch_text(json.loads(r["source_urls"] or "[]"))
        if not text:
            continue
        res = _extract_via_llm(_JUDGE.format(names=json.dumps(heirs, ensure_ascii=False), text=text[:9000]))
        verdicts = (res or {}).get("verdicts") or []
        if not verdicts:
            continue
        audited += 1
        for v in verdicts:
            role = (v.get("role") or "ausente").lower()
            role_counts[role] = role_counts.get(role, 0) + 1
            tot += 1
            if role == "heredero":
                good += 1
            else:
                flagged.append((r["id"], v.get("name"), role))

    print(f"\n=== HEIR ACCURACY AUDIT ({DB}) ===")
    print(f"Leads audited: {audited} | heir names checked: {tot}")
    if tot:
        print(f"Correct (named as heir): {good}/{tot} = {round(100*good/tot)}%")
        print(f"Roles: {role_counts}")
    if flagged:
        print(f"\nFlagged ({len(flagged)}) — name : real role:")
        for lid, name, role in flagged[:40]:
            print(f"  lead {lid}: {name!r} → {role}")
    print("\nAUDIT_JSON " + json.dumps(
        {"audited": audited, "checked": tot, "correct": good, "roles": role_counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
