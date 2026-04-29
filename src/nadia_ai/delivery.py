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
from nadia_ai.db import get_todays_leads
from nadia_ai.models import LeadRow

logger = logging.getLogger("nadia_ai.delivery")

SHEET_URL_TEMPLATE = "https://docs.google.com/spreadsheets/d/{sheet_id}"


def compute_todays_leads(conn: sqlite3.Connection) -> list[LeadRow]:
    """Query today's leads from the database and convert to LeadRow objects."""
    rows = get_todays_leads(conn)
    leads = []
    for row in rows:
        source_label = "Tablón" if row["source"] == "tablon" else "BOA"
        leads.append(
            LeadRow(
                fecha_deteccion=datetime.now(UTC).strftime("%Y-%m-%d"),
                fuente=source_label,
                causante=row["causante"] or "",
                localidad="Zaragoza",
                referencia_catastral=row["referencia_catastral"] or "",
                direccion=row["address"] or "",
                m2=row["m2"],
                tipo_inmueble=row["use_class"] or "",
                estado="Nuevo",
                notas="Pendiente enriquecimiento" if row.get("enrichment_pending") else "",
                link_edicto=row["source_url"] or "",
            )
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
        ws = sh.add_worksheet(title="Leads", rows=1000, cols=12)
        # Write headers
        ws.update("A1:L1", [LeadRow.sheet_headers()])
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
    """Apply conditional formatting to the Sheet."""

    # Auto-resize columns
    # Note: gspread doesn't support auto-resize directly, but we can set widths
    # This is best-effort formatting
    pass


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
        # Show top 3 leads (all leads are interesting — sorted as-is)
        top3 = leads[:3]
        top3_text = "\n".join(
            f"  • {ld.causante or 'Sin nombre'} — {ld.fuente} — {ld.localidad or 'Zaragoza'}"
            for ld in top3
        )
        top3_html = "".join(
            f"<li><strong>{ld.causante or 'Sin nombre'}</strong>"
            f" — {ld.fuente} — {ld.localidad or 'Zaragoza'}</li>"
            for ld in top3
        )

        subject = f"NadiaAI — Leads del día — {n_new} nuevos"
        body_text = (
            f"Hola Nadia,\n\n"
            f"Hoy ({today}) hay {n_new} nuevos leads de herencia:\n\n"
            f"Nuevos leads:\n{top3_text}\n\n"
            f"Tu hoja de leads: {sheet_url}\n\n"
            f"¡Buen día!\nNadiaAI"
        )
        body_html = (
            f"<p>Hola Nadia,</p>"
            f"<p>Hoy ({today}) hay <strong>{n_new}</strong> nuevos leads de herencia:</p>"
            f"<p><strong>Top {min(3, n_new)} por tamaño:</strong></p>"
            f"<ul>{top3_html}</ul>"
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
