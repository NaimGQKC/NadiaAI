"""Export FSBO ("venta por particular") listings to an agent-facing Excel.

For a listing agent, an owner already selling without an agent is the warmest
possible prospect for a mandate. This standalone tool scrapes pisos.com's
private-seller filter (see nadia_ai.scrapers.fsbo) and writes a single styled
sheet the agent can open and work top-down — sorted cheapest-first (most
motivated / fastest-moving inventory).

Self-contained: it does not touch nadia_ai.db or the edict pipeline.

Usage: python -m tools.fsbo_export   (writes exports/fsbo_particulares_<date>.xlsx)
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from nadia_ai.scrapers.fsbo import scrape_fsbo

# (header, column width)
HEADERS = [
    ("Título", 34), ("Precio", 14), ("m²", 8), ("Hab.", 7), ("Localidad", 20),
    ("Provincia", 20), ("Dirección", 28), ("Teléfono", 16), ("Anunciante", 14),
    ("Enlace", 14), ("Fecha", 12),
]

HDR_FILL = PatternFill("solid", fgColor="1F3864")
HDR_FONT = Font(color="FFFFFF", bold=True, size=11)
LINK_FONT = Font(color="2E86C1", underline="single", size=10)
BORDER = Border(bottom=Side(style="thin", color="DDDDDD"))
EUR = Alignment(horizontal="right", vertical="top")


def _sort_key(lead: dict):
    """Cheapest-first; listings without a price sink to the bottom."""
    price = lead.get("price_eur")
    return (0, price) if isinstance(price, int) else (1, 0)


def _write_sheet(ws, leads: list[dict]) -> None:
    for col, (h, w) in enumerate(HEADERS, 1):
        c = ws.cell(row=1, column=col, value=h)
        c.fill, c.font = HDR_FILL, HDR_FONT
        c.alignment = Alignment(horizontal="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(col)].width = w

    for i, lead in enumerate(leads, 2):
        price = lead.get("price_eur")
        row = [
            lead.get("title") or "—",
            price if isinstance(price, int) else "—",
            lead.get("m2") or "",
            lead.get("rooms") or "",
            lead.get("localidad") or "",
            lead.get("provincia") or "",
            lead.get("address") or "",
            lead.get("phone") or "—",
            "Particular",
            lead.get("listing_url") or "",
            lead.get("scraped_at") or "",
        ]
        for col, v in enumerate(row, 1):
            c = ws.cell(row=i, column=col, value=v)
            c.border = BORDER
            c.font = Font(bold=(col == 2), size=10)
            c.alignment = Alignment(wrap_text=(col in (1, 7)), vertical="top")
            if col == 2 and isinstance(v, int):
                c.number_format = '#,##0 "€"'
                c.alignment = EUR
            if col == 10 and v:  # Enlace -> clickable hyperlink
                c.value, c.font, c.hyperlink = "Ver anuncio", LINK_FONT, v

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{len(leads) + 1}"


def build(localidades: list[str] | None = None, max_per_loc: int = 60) -> str:
    """Scrape FSBO listings and write the styled xlsx; returns the output path."""
    leads = scrape_fsbo(localidades=localidades, max_per_loc=max_per_loc)
    leads.sort(key=_sort_key)

    wb = Workbook()
    ws = wb.active
    ws.title = "FSBO particulares"
    ws.sheet_properties.tabColor = "27AE60"
    _write_sheet(ws, leads)

    Path("exports").mkdir(exist_ok=True)
    out = f"exports/fsbo_particulares_{date.today().isoformat()}.xlsx"
    wb.save(out)
    return out


if __name__ == "__main__":
    print("Wrote", build())
