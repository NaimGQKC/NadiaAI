"""Export Aragón (or any-CCAA) heir leads to a client-ready Excel workbook.

Writes one row per quality-named heir in the target CCAA and tiers, with the deceased,
location, tier, and — where we resolved it — the verified email and its confidence
(high | medium). Emailed leads are sorted to the top. Read-only: never mutates the DB
and spends no credits.

Produces a formatted .xlsx (bold/frozen header, autofilter, sized columns) when
openpyxl is available, else falls back to a UTF-8 CSV Excel opens cleanly.

Usage (on PEDRO, where the persistent DB lives) — PowerShell:
    python tools/export_leads.py                         # Aragón, tiers A+B, all leads
    $env:HEIR_EXPORT_EMAILED_ONLY="1"; python tools/export_leads.py   # only emailed
    $env:HEIR_EXPORT_CONF="high";      python tools/export_leads.py   # only high-confidence
    $env:HEIR_EXPORT_CCAA="";          python tools/export_leads.py   # all CCAAs

Env:
    HEIR_EXPORT_CCAA          CCAA scope; default "aragon". Blank = all CCAAs.
    HEIR_EXPORT_TIERS         comma list of tiers to include; default "A,B".
    HEIR_EXPORT_EMAILED_ONLY  "1" = only rows with an email; default "0" (full A/B list).
    HEIR_EXPORT_CONF          "high" = only high-confidence emails; default = high+medium.
    HEIR_EXPORT_OUT           output path; default = ./nadia_leads_export.xlsx
    NADIA_DB_PATH             persistent DB path (set by the workflow).
"""

from __future__ import annotations

import csv
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(__file__))  # tools/  (for test_heir_phones helpers)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from nadia_ai.config import DB_PATH  # noqa: E402

# Reuse the exact CCAA / name-quality tests the enrichment uses, so the export scope
# matches the run scope and no junk names reach the client.
import test_heir_phones as tp  # noqa: E402

CCAA = os.getenv("HEIR_EXPORT_CCAA", "aragon").strip().lower()
TIERS = [t.strip().upper() for t in os.getenv("HEIR_EXPORT_TIERS", "A,B").split(",") if t.strip()]
EMAILED_ONLY = os.getenv("HEIR_EXPORT_EMAILED_ONLY", "0").strip() == "1"
CONF = (os.getenv("HEIR_EXPORT_CONF") or "").strip().lower()   # "" = high+medium
OUT = os.getenv("HEIR_EXPORT_OUT") or os.path.join(os.getcwd(), "nadia_leads_export.xlsx")

# Preferred columns, in client-facing order; only those present are emitted.
_WANT = [
    ("causante", "Deceased"),
    ("heir_name", "Heir"),
    ("localidad", "Town"),
    ("region", "Region"),
    ("tier", "Tier"),
    ("contact_email", "Email"),
    ("contact_confidence", "Email confidence"),
    ("date_of_death", "Date of death"),
    ("referencia_catastral", "Catastro ref"),
    ("direccion", "Property address"),
]


def _cols(conn: sqlite3.Connection) -> list:
    have = {r[1] for r in conn.execute("PRAGMA table_info(leads)").fetchall()}
    return [(c, label) for c, label in _WANT if c in have]


def _cell(r, col: str):
    """Value for one output cell. For the property-address column, only surface a
    REAL street-level address (one with a house number). A bare city/region name
    ("Zaragoza") is not an address — showing it there misleads the client into
    thinking we have a street, so blank it. The street itself, when the public
    source never published one, has to come from a postal-append provider (Deyde)."""
    v = r[col]
    if col == "direccion":
        s = (v or "").strip()
        if not s or not any(ch.isdigit() for ch in s):
            return ""
    return v


def _fetch(conn: sqlite3.Connection, cols: list) -> list:
    select = ", ".join(c for c, _ in cols)
    tier_ph = ",".join("?" for _ in TIERS)
    where = ["heir_name IS NOT NULL AND heir_name != ''"]
    if TIERS:
        where.append(f"tier IN ({tier_ph})")
    if EMAILED_ONLY:
        where.append("contact_email IS NOT NULL AND contact_email != ''")
    rows = conn.execute(
        f"SELECT {select} FROM leads WHERE {' AND '.join(where)}",
        tuple(TIERS),
    ).fetchall()

    def _has(r, k):
        return k in r.keys()

    def _keep(r) -> bool:
        if CCAA and not tp._in_ccaa(r["region"] if _has(r, "region") else "",
                                    r["localidad"] if _has(r, "localidad") else "", CCAA):
            return False
        if not tp._good_heir_name(r["heir_name"] if _has(r, "heir_name") else ""):
            return False
        conf = (r["contact_confidence"] or "") if _has(r, "contact_confidence") else ""
        if CONF == "high" and "high" not in conf.lower():
            return False
        return True

    def _rank(r):
        # Emailed first (high before medium), then the rest — most actionable on top.
        conf = (r["contact_confidence"] or "").lower() if _has(r, "contact_confidence") else ""
        has_email = bool(r["contact_email"]) if _has(r, "contact_email") else False
        conf_rank = 0 if "high" in conf else (1 if "medium" in conf else 2)
        return (0 if has_email else 1, conf_rank,
                (r["region"] or "") if _has(r, "region") else "",
                (r["localidad"] or "") if _has(r, "localidad") else "")

    return sorted((r for r in _fetch_keep(rows, _keep)), key=_rank)


def _fetch_keep(rows, keep):
    return [r for r in rows if keep(r)]


def _write_xlsx(path: str, cols: list, rows: list) -> bool:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except Exception:
        return False

    wb = Workbook()
    ws = wb.active
    ws.title = (CCAA or "all").title()[:31]
    headers = [label for _, label in cols]
    ws.append(headers)

    head_fill = PatternFill("solid", fgColor="1F4E79")
    head_font = Font(bold=True, color="FFFFFF")
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = head_fill
        cell.font = head_font
        cell.alignment = Alignment(vertical="center")

    hi_fill = PatternFill("solid", fgColor="E2EFDA")   # green-ish = high confidence
    med_fill = PatternFill("solid", fgColor="FFF2CC")  # amber-ish = medium
    conf_idx = next((i for i, (c, _) in enumerate(cols) if c == "contact_confidence"), None)

    for r in rows:
        ws.append([_cell(r, c) for c, _ in cols])
        if conf_idx is not None:
            conf = (r["contact_confidence"] or "").lower()
            fill = hi_fill if "high" in conf else (med_fill if "medium" in conf else None)
            if fill:
                ws.cell(row=ws.max_row, column=conf_idx + 1).fill = fill

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{ws.max_row}"
    # Size columns to their content (capped) so the client can read it as-is.
    for i, (c, label) in enumerate(cols, 1):
        longest = max([len(label)] + [len(str(_cell(r, c) or "")) for r in rows] or [0])
        ws.column_dimensions[get_column_letter(i)].width = min(max(longest + 2, 10), 48)

    wb.save(path)
    return True


def _write_csv(path: str, cols: list, rows: list) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # BOM => Excel UTF-8 clean
        w = csv.writer(f)
        w.writerow([label for _, label in cols])
        for r in rows:
            w.writerow([_cell(r, c) for c, _ in cols])


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cols = _cols(conn)
    if not cols:
        print(f"ERROR: no 'leads' table (or none of the expected columns) at {DB_PATH}. "
              "Run this on PEDRO where the persistent DB lives.", file=sys.stderr)
        return 2

    rows = _fetch(conn, cols)

    out = OUT
    if _write_xlsx(out, cols, rows):
        fmt = "xlsx"
    else:
        # openpyxl unavailable — write a CSV Excel opens cleanly instead.
        out = os.path.splitext(OUT)[0] + ".csv"
        _write_csv(out, cols, rows)
        fmt = "csv (openpyxl not installed — `pip install openpyxl` for a formatted .xlsx)"

    n_email = sum(1 for r in rows if r["contact_email"]) if any(c == "contact_email" for c, _ in cols) else 0
    n_high = sum(1 for r in rows
                 if "high" in ((r["contact_confidence"] or "").lower()
                               if "contact_confidence" in r.keys() else ""))
    scope = CCAA or "all CCAAs"
    print(f"Exported {len(rows)} leads (tiers {'+'.join(TIERS) or 'all'}, scope={scope}): "
          f"{n_email} with email ({n_high} high, {n_email - n_high} medium) -> {out}  [{fmt}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
