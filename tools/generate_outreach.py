"""Generate the per-lead outreach pack — the agent's ready-to-send copy.

Pulls the hottest cohort (FSBO owners with a phone + top open-window inheritance
leads), renders channel-appropriate Spanish outreach for each (see
``nadia_ai.outreach``), and writes ``exports/outreach_<date>.xlsx`` with two
sheets: "Llamadas (FSBO)" (call script + WhatsApp) and "Cartas (Herencia)"
(letters / action notes). The LLM polish (if a key is set) runs ~once per type —
cheap and privacy-safe (placeholders only).

Usage:
    python -m tools.generate_outreach            # deterministic templates
    python -m tools.generate_outreach --llm      # LLM-polished copy
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from nadia_ai.outreach import lead_type, render_outreach

DB = "nadia_ai.db"

HDR_FILL = PatternFill("solid", fgColor="1F3864")
HDR_FONT = Font(color="FFFFFF", bold=True, size=11)
CALL_FILL = PatternFill("solid", fgColor="D5F5E3")   # FSBO — call now (green)
LETTER_FILL = PatternFill("solid", fgColor="FCF3CF")  # letters (amber)

# (header, width, wrap)
CALL_COLS = [
    ("Tipo", 20, False), ("Inmueble / zona", 24, True), ("Precio", 12, False),
    ("Teléfono", 15, False), ("Guion de llamada", 70, True),
    ("Mensaje WhatsApp", 55, True), ("Notas", 40, True), ("Enlace", 14, False),
]
LETTER_COLS = [
    ("Tipo", 20, False), ("Causante", 26, False), ("Localidad", 16, False),
    ("Canal", 22, False), ("Asunto", 34, True), ("Carta / mensaje", 80, True),
    ("Contacto (notaría/juzgado)", 34, True), ("Notas", 40, True), ("Enlace", 14, False),
]


def _inheritance_cohort(conn: sqlite3.Connection, limit: int) -> list[dict]:
    """Top named inheritance/obituary leads, soonest legal deadline first."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT * FROM leads
        WHERE (TRIM(COALESCE(causante,'')) <> '' OR TRIM(COALESCE(heir_name,'')) <> '')
        ORDER BY
            CASE WHEN edict_window_days IS NOT NULL AND edict_window_days >= 0 THEN 0 ELSE 1 END,
            edict_window_days ASC,
            first_seen_at DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def _fsbo_cohort(limit: int) -> list[dict]:
    """Fresh FSBO listings with an owner phone (best-effort; network)."""
    try:
        from nadia_ai.scrapers.fsbo import scrape_fsbo

        leads = scrape_fsbo(max_per_loc=limit, enrich_phones=True)
        # Phone-first: a callable owner is the whole point of this sheet.
        leads.sort(key=lambda x: (x.get("phone") is None, x.get("price_eur") or 1e12))
        return leads[:limit]
    except Exception as e:  # noqa: BLE001 — FSBO is a bonus feed, never fail the pack
        import logging

        logging.getLogger("nadia_ai.outreach").warning("FSBO cohort skipped: %s", e)
        return []


def _style_header(ws, cols) -> None:
    for c, (h, w, _) in enumerate(cols, 1):
        cell = ws.cell(1, c, h)
        cell.fill, cell.font = HDR_FILL, HDR_FONT
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{1}"


def _write_row(ws, r: int, values: list, cols, fill) -> None:
    for c, (val, (_, _, wrap)) in enumerate(zip(values, cols), 1):
        cell = ws.cell(r, c, val)
        cell.fill = fill
        cell.alignment = Alignment(wrap_text=wrap, vertical="top")
        cell.font = Font(size=10)


def build(limit_inh: int = 30, limit_fsbo: int = 25, use_llm: bool = False, db_path: str = DB) -> str:
    conn = sqlite3.connect(db_path)
    inh = _inheritance_cohort(conn, limit_inh)
    conn.close()
    fsbo = _fsbo_cohort(limit_fsbo)

    wb = Workbook()

    # Sheet 1 — FSBO call list.
    ws_call = wb.active
    ws_call.title = "Llamadas (FSBO)"
    _style_header(ws_call, CALL_COLS)
    ws_call.sheet_properties.tabColor = "27AE60"
    r = 2
    for lead in fsbo:
        o = render_outreach(lead, use_llm=use_llm)
        url = lead.get("listing_url") or ""
        _write_row(ws_call, r, [
            o["tipo"], lead.get("address") or lead.get("localidad") or "",
            (f"{lead.get('price_eur'):,}".replace(",", ".") + " €") if lead.get("price_eur") else "",
            lead.get("phone") or "—", o["guion_llamada"], o["mensaje"], o["notas"], url,
        ], CALL_COLS, CALL_FILL)
        if url:
            cell = ws_call.cell(r, len(CALL_COLS))
            cell.value, cell.hyperlink = "Ver anuncio", url
            cell.font = Font(color="2E86C1", underline="single", size=10)
        r += 1
    ws_call.auto_filter.ref = f"A1:{get_column_letter(len(CALL_COLS))}{max(r - 1, 1)}"

    # Sheet 2 — inheritance / obituary letters.
    ws_let = wb.create_sheet("Cartas (Herencia)")
    _style_header(ws_let, LETTER_COLS)
    r = 2
    for lead in inh:
        o = render_outreach(lead, use_llm=use_llm)
        import json as _json

        urls = []
        try:
            urls = _json.loads(lead.get("source_urls") or "[]")
        except (_json.JSONDecodeError, TypeError):
            urls = []
        url = urls[0] if urls else ""
        _write_row(ws_let, r, [
            o["tipo"], lead.get("causante") or "—", lead.get("localidad") or lead.get("region") or "",
            o["canal"], o["asunto"], o["mensaje"], lead.get("juzgado") or "—", o["notas"], url,
        ], LETTER_COLS, LETTER_FILL)
        if url:
            cell = ws_let.cell(r, len(LETTER_COLS))
            cell.value, cell.hyperlink = "Ver edicto", url
            cell.font = Font(color="2E86C1", underline="single", size=10)
        r += 1
    ws_let.auto_filter.ref = f"A1:{get_column_letter(len(LETTER_COLS))}{max(r - 1, 1)}"

    Path("exports").mkdir(exist_ok=True)
    out = f"exports/outreach_{date.today().isoformat()}.xlsx"
    wb.save(out)
    return out


if __name__ == "__main__":
    path = build(use_llm="--llm" in sys.argv)
    print("Wrote", path)
