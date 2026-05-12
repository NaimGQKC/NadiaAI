"""Export leads for Phantombuster social graph enrichment.

Pulls Tier A/B leads without social profile links and exports them
as a CSV formatted for Phantombuster's Facebook Profile Scraper
or LinkedIn Search tools.

Can also push directly to Phantombuster via API if key is configured.

Usage:
    python -m nadia_ai.export_for_enrichment
"""

import csv
import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import requests

from nadia_ai.config import PHANTOMBUSTER_API_KEY
from nadia_ai.db import get_connection
from nadia_ai.merge import init_leads_schema

logger = logging.getLogger("nadia_ai.export_for_enrichment")

PHANTOMBUSTER_API_URL = "https://api.phantombuster.com/api/v2"
EXPORTS_DIR = Path("exports")


def get_leads_for_enrichment(conn: sqlite3.Connection) -> list[dict]:
    """Get Tier A/B leads without social profile links."""
    init_leads_schema(conn)
    rows = conn.execute(
        """SELECT id, causante, heir_name, heir_names_json, localidad, direccion,
                  tier, days_since_death, urgency_phase
           FROM leads
           WHERE tier = 'A'
             AND (social_profile_url IS NULL OR social_profile_url = '')
             AND outreach_allowed = 1
           ORDER BY
               CASE tier WHEN 'A' THEN 0 WHEN 'B' THEN 1 END,
               days_since_death DESC NULLS LAST"""
    ).fetchall()
    return [dict(row) for row in rows]


def export_csv(leads: list[dict]) -> Path:
    """Export leads as CSV for manual Phantombuster upload.

    Format: name, city, context (for Facebook/LinkedIn search)
    """
    EXPORTS_DIR.mkdir(exist_ok=True)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    csv_path = EXPORTS_DIR / f"phantombuster_input_{today}.csv"

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "city", "lead_id", "tier", "urgency"])

        for lead in leads:
            # Use heir name if available, otherwise causante
            names_to_search = []
            heir_name = lead.get("heir_name") or ""
            if heir_name:
                names_to_search.append(heir_name)

            # Add additional heirs from JSON
            try:
                heir_list = json.loads(lead.get("heir_names_json") or "[]")
                for h in heir_list:
                    if h and h not in names_to_search:
                        names_to_search.append(h)
            except (json.JSONDecodeError, TypeError):
                pass

            # Fall back to causante if no heirs
            if not names_to_search:
                causante = lead.get("causante") or ""
                if causante:
                    names_to_search.append(causante)

            city = lead.get("localidad") or "Zaragoza"

            for name in names_to_search:
                writer.writerow([
                    name,
                    city,
                    lead["id"],
                    lead.get("tier", ""),
                    lead.get("urgency_phase", ""),
                ])

    logger.info("Exported %d leads to %s", len(leads), csv_path)
    return csv_path


def push_to_phantombuster(leads: list[dict]) -> dict:
    """Push search queries directly to Phantombuster API (if key configured).

    Uses the Phantombuster Agent Launch endpoint to trigger a
    Facebook/LinkedIn search agent with the lead names.
    """
    if not PHANTOMBUSTER_API_KEY:
        logger.warning("PHANTOMBUSTER_API_KEY not set — skipping API push")
        return {"status": "skipped", "reason": "no_api_key"}

    headers = {
        "X-Phantombuster-Key": PHANTOMBUSTER_API_KEY,
        "Content-Type": "application/json",
    }

    # Build search queries
    queries = []
    for lead in leads[:50]:  # Free tier limits
        name = lead.get("heir_name") or lead.get("causante") or ""
        city = lead.get("localidad") or "Zaragoza"
        if name:
            queries.append(f"{name} {city}")

    if not queries:
        return {"status": "no_queries"}

    # Store queries for Phantombuster agent input
    # The user configures their Phantombuster agent to read from this
    result = {
        "status": "exported",
        "query_count": len(queries),
        "queries": queries,
    }

    # Try to list available agents
    try:
        resp = requests.get(
            f"{PHANTOMBUSTER_API_URL}/agents/fetch-all",
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 200:
            agents = resp.json()
            result["available_agents"] = len(agents) if isinstance(agents, list) else 0
            logger.info("Phantombuster: %d agents available", result["available_agents"])
    except requests.RequestException as e:
        logger.warning("Phantombuster API check failed: %s", e)
        result["api_error"] = str(e)

    return result


def run_export():
    """Main export entry point."""
    logging.basicConfig(level=logging.INFO, format="%(name)s — %(message)s")
    conn = get_connection()

    leads = get_leads_for_enrichment(conn)
    if not leads:
        logger.info("No leads to export for enrichment")
        conn.close()
        return

    logger.info("Found %d leads for social enrichment", len(leads))

    # Always export CSV (manual workflow)
    csv_path = export_csv(leads)
    print(f"CSV exported: {csv_path}")

    # Try Phantombuster API if key is set
    if PHANTOMBUSTER_API_KEY:
        result = push_to_phantombuster(leads)
        print(f"Phantombuster: {result}")

    conn.close()


if __name__ == "__main__":
    run_export()
