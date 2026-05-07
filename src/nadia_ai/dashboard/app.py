"""NadiaAI Dashboard — Simple Flask Kanban for inheritance leads.

Run with: python -m nadia_ai.dashboard.app
"""

import json
import logging
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from nadia_ai.config import DASHBOARD_PORT, DB_PATH
from nadia_ai.db import get_connection
from nadia_ai.merge import init_leads_schema

logger = logging.getLogger("nadia_ai.dashboard")

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

app = Flask(
    __name__,
    template_folder=str(TEMPLATE_DIR),
    static_folder=str(STATIC_DIR),
)


def _get_conn():
    """Get a read/write connection to the leads DB."""
    conn = get_connection()
    init_leads_schema(conn)
    return conn


@app.route("/")
def index():
    """Serve the Kanban board."""
    return render_template("index.html")


@app.route("/api/leads")
def api_leads():
    """JSON feed of all actionable leads, grouped for Kanban columns."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT id, tier, causante, heir_name, direccion, localidad,
                      referencia_catastral, fecha_fallecimiento, date_of_death,
                      days_since_death, urgency_phase, tax_deadline,
                      sources, social_profile_url, kanban_status,
                      outreach_allowed, outreach_notes, m2, use_class,
                      first_seen_at, last_updated_at, estado, notas
               FROM leads
               WHERE tier IN ('A', 'B', 'X')
               ORDER BY
                   CASE tier WHEN 'A' THEN 0 WHEN 'B' THEN 1 WHEN 'X' THEN 2 END,
                   days_since_death DESC NULLS LAST"""
        ).fetchall()

        leads = []
        for row in rows:
            lead = dict(row)
            lead["sources"] = json.loads(lead.get("sources") or "[]")
            leads.append(lead)

        # Group into Kanban columns
        columns = {
            "new_to_call": [],
            "tax_followup": [],
            "urgent": [],
            "signed": [],
        }

        for lead in leads:
            status = lead.get("kanban_status") or "new_to_call"
            days = lead.get("days_since_death")

            if status == "signed":
                columns["signed"].append(lead)
            elif days is not None and days >= 150:
                columns["urgent"].append(lead)
            elif days is not None and 91 <= days < 150:
                columns["tax_followup"].append(lead)
            else:
                columns[status if status in columns else "new_to_call"].append(lead)

        return jsonify({
            "columns": columns,
            "total": len(leads),
            "stats": {
                "tier_a": sum(1 for l in leads if l["tier"] == "A"),
                "tier_b": sum(1 for l in leads if l["tier"] == "B"),
                "tier_x": sum(1 for l in leads if l["tier"] == "X"),
                "urgent": len(columns["urgent"]),
            },
        })
    finally:
        conn.close()


@app.route("/api/leads/<int:lead_id>/status", methods=["POST"])
def update_status(lead_id: int):
    """Update Kanban status for a lead (drag-and-drop)."""
    data = request.get_json()
    new_status = data.get("status", "new_to_call")

    valid_statuses = {"new_to_call", "tax_followup", "urgent", "signed"}
    if new_status not in valid_statuses:
        return jsonify({"error": f"Invalid status: {new_status}"}), 400

    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE leads SET kanban_status = ?, last_updated_at = datetime('now') WHERE id = ?",
            (new_status, lead_id),
        )
        # If marked as signed, update estado too
        if new_status == "signed":
            conn.execute(
                "UPDATE leads SET estado = 'Firmado' WHERE id = ?", (lead_id,)
            )
        conn.commit()
        return jsonify({"ok": True, "lead_id": lead_id, "status": new_status})
    finally:
        conn.close()


@app.route("/api/leads/<int:lead_id>/notes", methods=["POST"])
def update_notes(lead_id: int):
    """Update agent notes for a lead."""
    data = request.get_json()
    notes = data.get("notes", "")

    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE leads SET notas = ?, last_updated_at = datetime('now') WHERE id = ?",
            (notes, lead_id),
        )
        conn.commit()
        return jsonify({"ok": True, "lead_id": lead_id})
    finally:
        conn.close()


@app.route("/api/stats")
def api_stats():
    """Pipeline stats summary."""
    conn = _get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) as c FROM leads").fetchone()["c"]
        tier_a = conn.execute("SELECT COUNT(*) as c FROM leads WHERE tier = 'A'").fetchone()["c"]
        tier_b = conn.execute("SELECT COUNT(*) as c FROM leads WHERE tier = 'B'").fetchone()["c"]
        urgent = conn.execute(
            "SELECT COUNT(*) as c FROM leads WHERE days_since_death >= 150"
        ).fetchone()["c"]
        today = conn.execute(
            "SELECT COUNT(*) as c FROM leads WHERE date(first_seen_at) = date('now')"
        ).fetchone()["c"]

        return jsonify({
            "total_leads": total,
            "tier_a": tier_a,
            "tier_b": tier_b,
            "urgent": urgent,
            "new_today": today,
        })
    finally:
        conn.close()


def main():
    """Run the dashboard server."""
    logging.basicConfig(level=logging.INFO, format="%(name)s — %(message)s")
    print(f"\n  NadiaAI Dashboard -> http://localhost:{DASHBOARD_PORT}\n")
    app.run(host="0.0.0.0", port=DASHBOARD_PORT, debug=True)


if __name__ == "__main__":
    main()
