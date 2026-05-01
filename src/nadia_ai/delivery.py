"""Google Sheets + email delivery for daily lead summaries.

Writes new leads to a shared Google Sheet and sends a morning email
to Nadia with the top leads and a link to the Sheet.
"""

import csv
import logging
import smtplib
import sqlite3
from datetime import UTC, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from nadia_ai.config import (
    DEV_ALERT_EMAIL,
    GOOGLE_SERVICE_ACCOUNT_JSON,
    LEADS_SHEET_ID,
    MAMA_EMAIL,
    SMTP_PASSWORD,
    SMTP_USER,
)
from nadia_ai.merge import get_todays_leads
from nadia_ai.models import LeadRow

logger = logging.getLogger("nadia_ai.delivery")

SHEET_URL_TEMPLATE = "https://docs.google.com/spreadsheets/d/{sheet_id}"


def compute_todays_leads(conn: sqlite3.Connection) -> list[LeadRow]:
    """Query today's leads from the merged leads table.

    Reads from the deduplicated, tier-classified leads table.
    Only returns leads first seen today (daily delta — no re-emission).
    """
    import json

    rows = get_todays_leads(conn)
    leads = []
    for row in rows:
        sources = json.loads(row["sources"])
        source_urls = json.loads(row["source_urls"])
        leads.append(
            LeadRow(
                tier=row["tier"],
                fecha_deteccion=row["first_seen_at"][:10],
                fuentes=", ".join(sources),
                causante=row["causante"] or "",
                fecha_fallecimiento=row["fecha_fallecimiento"] or "",
                localidad=row["localidad"] or "",
                direccion=row["direccion"] or "",
                referencia_catastral=row["referencia_catastral"] or "",
                m2=row["m2"],
                tipo_inmueble=row["use_class"] or "",
                estado=row["estado"] or "Nuevo",
                outreach_ok="Sí" if row["outreach_allowed"] else "No",
                notas_sistema=row["outreach_notes"] or "",
                notas=row["notas"] or "",
                link_edicto=source_urls[0] if source_urls else "",
                subasta_activa=row.get("subasta_activa") or "",
                obras_recientes=row.get("obras_recientes") or "",
            )
        )

    logger.info("Today's leads: %d (A=%d, B=%d, C=%d, X=%d)",
        len(leads),
        sum(1 for l in leads if l.tier == "A"),
        sum(1 for l in leads if l.tier == "B"),
        sum(1 for l in leads if l.tier == "C"),
        sum(1 for l in leads if l.tier == "X"),
    )
    return leads


def deliver(leads: list[LeadRow], summary: dict) -> None:
    """Deliver leads to Google Sheets and send email summary.

    Fallback chain: if Sheets write fails, write CSV locally and email as attachment.
    """
    sheet_ok = False

    # Step 1: Try Google Sheets
    if GOOGLE_SERVICE_ACCOUNT_JSON and LEADS_SHEET_ID:
        try:
            write_to_sheets(leads)
            sheet_ok = True
        except Exception as e:
            logger.error("Google Sheets write failed: %s", e)
            summary["errors"].append(f"sheets: {e}")

    if not sheet_ok:
        if not GOOGLE_SERVICE_ACCOUNT_JSON or not LEADS_SHEET_ID:
            logger.warning("Google Sheets not configured — writing CSV fallback")
        csv_path = write_csv_fallback(leads)
        logger.info("CSV fallback written to %s", csv_path)

    # Step 2: Send email
    if MAMA_EMAIL and SMTP_USER and SMTP_PASSWORD:
        try:
            send_email(leads, sheet_ok)
        except Exception as e:
            logger.error("Email send failed: %s", e)
            summary["errors"].append(f"email: {e}")

            # Alert dev if configured
            if DEV_ALERT_EMAIL:
                try:
                    send_dev_alert(str(e))
                except Exception:
                    logger.error("Dev alert email also failed")
    else:
        logger.warning("Email not configured — skipping email delivery")


def write_to_sheets(leads: list[LeadRow]) -> None:
    """Append new leads to the Google Sheet."""
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    creds = Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_JSON, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(LEADS_SHEET_ID)

    # Get or create the Leads tab
    try:
        ws = sh.worksheet("Leads")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="Leads", rows=1000, cols=20)
        # Write headers (15 columns for Phase 2)
        headers = LeadRow.sheet_headers()
        end_col = chr(ord("A") + len(headers) - 1)
        ws.update(f"A1:{end_col}1", [headers])
        # Freeze header row
        ws.freeze(rows=1)
        logger.info("Created 'Leads' worksheet with headers")

    # Read existing causante names to avoid duplicates
    existing_causantes = set()
    try:
        col_values = ws.col_values(3)  # Column C = Causante
        existing_causantes = set(col_values[1:])  # Skip header
    except Exception:
        pass

    # Filter to only new leads (dedup by causante name)
    new_leads = [lead for lead in leads if lead.causante not in existing_causantes]

    if not new_leads:
        logger.info("No new leads to append to Sheet")
        return

    # Append rows
    rows = [lead.to_row() for lead in new_leads]
    ws.append_rows(rows, value_input_option="USER_ENTERED")
    logger.info("Appended %d new leads to Google Sheet", len(new_leads))

    # Apply formatting to new rows
    try:
        _apply_formatting(ws)
    except Exception as e:
        logger.warning("Sheet formatting failed (non-critical): %s", e)


def _apply_formatting(ws) -> None:
    """Apply conditional formatting to the Sheet for tier-based coloring."""
    from gspread_formatting import (
        BooleanCondition,
        BooleanRule,
        CellFormat,
        Color,
        ConditionalFormatRule,
        GridRange,
        get_conditional_format_rules,
        set_conditional_format_rules,
    )

    rules = get_conditional_format_rules(ws)

    # Tier X rows → gray background (column A = "X")
    rules.append(
        ConditionalFormatRule(
            ranges=[GridRange.from_a1_range("A2:O1000", ws)],
            booleanRule=BooleanRule(
                condition=BooleanCondition("CUSTOM_FORMULA", ['=$A2="X"']),
                format=CellFormat(backgroundColor=Color(0.85, 0.85, 0.85)),
            ),
        )
    )

    # Tier A rows → light green background
    rules.append(
        ConditionalFormatRule(
            ranges=[GridRange.from_a1_range("A2:O1000", ws)],
            booleanRule=BooleanRule(
                condition=BooleanCondition("CUSTOM_FORMULA", ['=$A2="A"']),
                format=CellFormat(backgroundColor=Color(0.85, 0.94, 0.85)),
            ),
        )
    )

    set_conditional_format_rules(ws, rules)


def write_calibracion_tab(calibration_rows: list[dict]) -> None:
    """Write INE calibration data to the 'Calibracion' tab in Google Sheets.

    This tab is a read-only dashboard for the dev to monitor pipeline health.
    """
    if not GOOGLE_SERVICE_ACCOUNT_JSON or not LEADS_SHEET_ID:
        logger.warning("Google Sheets not configured — skipping Calibración tab")
        return

    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_JSON, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(LEADS_SHEET_ID)

    headers = [
        "Periodo",
        "Defunciones Zaragoza",
        "ETDP Herencias Zaragoza",
        "Leads pipeline",
        "Cobertura %",
        "Alerta",
    ]

    try:
        ws = sh.worksheet("Calibración")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="Calibración", rows=100, cols=len(headers))
        ws.freeze(rows=1)
        logger.info("Created 'Calibración' worksheet")

    # Clear and rewrite (small dataset, idempotent)
    ws.clear()
    rows = [headers]
    for row in calibration_rows:
        rows.append([
            row.get("periodo", ""),
            row.get("defunciones_zaragoza", ""),
            row.get("etdp_herencias_zaragoza", ""),
            row.get("leads_pipeline", ""),
            f"{row.get('reach_pct', 0):.1f}%" if row.get("reach_pct") else "",
            row.get("outage_flag", ""),
        ])
    ws.update(f"A1:F{len(rows)}", rows, value_input_option="USER_ENTERED")
    logger.info("Calibración tab updated with %d rows", len(calibration_rows))


def write_csv_fallback(leads: list[LeadRow]) -> Path:
    """Write leads to a local CSV as fallback."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    csv_path = Path(f"leads_{today}.csv")

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(LeadRow.sheet_headers())
        for lead in leads:
            writer.writerow(lead.to_row())

    return csv_path


def send_email(leads: list[LeadRow], sheet_ok: bool) -> None:
    """Send the daily lead summary email to Nadia."""
    today = datetime.now(UTC).strftime("%d/%m/%Y")
    n_new = len(leads)
    sheet_url = SHEET_URL_TEMPLATE.format(sheet_id=LEADS_SHEET_ID)

    if n_new == 0:
        subject = "NadiaAI — Leads del día — Hoy sin novedades"
        body_text = (
            f"Hola Nadia,\n\n"
            f"Hoy ({today}) no hay nuevos leads de herencia.\n\n"
            f"Tu hoja de leads: {sheet_url}\n\n"
            f"¡Buen día!\nNadiaAI"
        )
        body_html = (
            f"<p>Hola Nadia,</p>"
            f"<p>Hoy ({today}) no hay nuevos leads de herencia.</p>"
            f'<p><a href="{sheet_url}">Abrir hoja de leads</a></p>'
            f"<p>¡Buen día!<br>NadiaAI</p>"
        )
    else:
        tier_a = [ld for ld in leads if ld.tier == "A"]
        tier_b = [ld for ld in leads if ld.tier == "B"]

        # Show top Tier A leads, then top B
        top_leads = (tier_a + tier_b)[:5]
        top_text = "\n".join(
            f"  [{ld.tier}] {ld.causante or 'Sin nombre'} — {ld.fuentes} — {ld.localidad or 'Zaragoza'}"
            + (f" — {ld.direccion}" if ld.direccion else "")
            for ld in top_leads
        )
        top_html = "".join(
            f"<li><span style='color:{'#2e7d32' if ld.tier == 'A' else '#f57c00'}'>[{ld.tier}]</span> "
            f"<strong>{ld.causante or 'Sin nombre'}</strong>"
            f" — {ld.fuentes} — {ld.localidad or 'Zaragoza'}"
            + (f" — <em>{ld.direccion}</em>" if ld.direccion else "")
            + "</li>"
            for ld in top_leads
        )

        # Collect active source names
        all_sources = set()
        for ld in leads:
            all_sources.update(s.strip() for s in ld.fuentes.split(",") if s.strip())
        fuentes_line = ", ".join(sorted(all_sources))

        subject = f"NadiaAI — {len(tier_a)} nuevos accionables ({n_new} totales)"
        body_text = (
            f"Hola Nadia,\n\n"
            f"Hoy ({today}) hay {n_new} nuevos leads"
            f" ({len(tier_a)} Tier A, {len(tier_b)} Tier B):\n\n"
            f"Top leads:\n{top_text}\n\n"
            f"Fuentes activas: {fuentes_line}\n\n"
            f"Tu hoja de leads: {sheet_url}\n\n"
            f"¡Buen día!\nNadiaAI"
        )
        body_html = (
            f"<p>Hola Nadia,</p>"
            f"<p>Hoy ({today}) hay <strong>{n_new}</strong> nuevos leads"
            f" (<strong>{len(tier_a)}</strong> Tier A, {len(tier_b)} Tier B):</p>"
            f"<ul>{top_html}</ul>"
            f"<p style='color:#666'>Fuentes activas: {fuentes_line}</p>"
            f'<p><a href="{sheet_url}">Abrir hoja de leads</a></p>'
            f"<p>¡Buen día!<br>NadiaAI</p>"
        )

    _send_smtp(MAMA_EMAIL, subject, body_text, body_html)
    logger.info("Daily email sent to Nadia — %d leads", n_new)


def send_dev_alert(error_msg: str) -> None:
    """Send a failure alert to the developer."""
    if not DEV_ALERT_EMAIL:
        return
    subject = "NadiaAI — Pipeline error"
    body = f"Pipeline failed:\n\n{error_msg}"
    _send_smtp(DEV_ALERT_EMAIL, subject, body, f"<p>{body}</p>")


def _send_smtp(to: str, subject: str, body_text: str, body_html: str) -> None:
    """Send an email via Gmail SMTP."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = to
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    # Port 465 (SSL) preferred over 587 (STARTTLS) — more reliable from GitHub Actions runners
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
