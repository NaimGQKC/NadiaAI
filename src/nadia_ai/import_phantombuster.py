"""Import Phantombuster results back into the leads database.

Reads a Phantombuster output CSV (profile URLs matched by name)
and updates the social_profile_url column in the leads table.

Usage:
    python -m nadia_ai.import_phantombuster <path_to_results.csv>
"""

import csv
import logging
import sqlite3
import sys
from pathlib import Path

from nadia_ai.db import get_connection
from nadia_ai.merge import init_leads_schema, normalize_name

logger = logging.getLogger("nadia_ai.import_phantombuster")


def import_results(conn: sqlite3.Connection, csv_path: Path) -> dict:
    """Import Phantombuster results CSV into the leads table.

    Expected CSV columns (flexible — tries multiple patterns):
    - name / query / search: the original search name
    - profileUrl / url / link: the found social profile URL
    - lead_id: (optional) direct lead ID reference

    Returns stats dict.
    """
    init_leads_schema(conn)
    stats = {"matched": 0, "unmatched": 0, "skipped": 0, "total": 0}

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        headers_lower = [h.lower().strip() for h in headers]

        # Find column mappings (Phantombuster output varies by agent)
        name_col = None
        url_col = None
        id_col = None

        for h in headers:
            hl = h.lower().strip()
            if hl in ("name", "query", "search", "fullname", "full_name"):
                name_col = h
            elif hl in ("profileurl", "url", "link", "profile_url", "facebook_url", "linkedin_url"):
                url_col = h
            elif hl in ("lead_id", "leadid", "id"):
                id_col = h

        if not url_col:
            logger.error("No profile URL column found in CSV. Headers: %s", headers)
            return stats

        for row in reader:
            stats["total"] += 1
            profile_url = (row.get(url_col) or "").strip()

            if not profile_url:
                stats["skipped"] += 1
                continue

            # Try to match by lead_id first
            matched = False
            if id_col and row.get(id_col):
                try:
                    lead_id = int(row[id_col])
                    existing = conn.execute(
                        "SELECT id FROM leads WHERE id = ?", (lead_id,)
                    ).fetchone()
                    if existing:
                        conn.execute(
                            "UPDATE leads SET social_profile_url = ?, last_updated_at = datetime('now') WHERE id = ?",
                            (profile_url, lead_id),
                        )
                        matched = True
                except (ValueError, TypeError):
                    pass

            # Fall back to name matching
            if not matched and name_col and row.get(name_col):
                search_name = normalize_name(row[name_col])
                if search_name:
                    # Try heir_name first, then causante
                    lead = conn.execute(
                        """SELECT id FROM leads
                           WHERE causante_norm = ? OR heir_name = ?
                           LIMIT 1""",
                        (search_name, row[name_col]),
                    ).fetchone()
                    if lead:
                        conn.execute(
                            "UPDATE leads SET social_profile_url = ?, last_updated_at = datetime('now') WHERE id = ?",
                            (profile_url, lead["id"]),
                        )
                        matched = True

            if matched:
                stats["matched"] += 1
                logger.info("Matched: %s → %s", row.get(name_col, "?"), profile_url[:60])
            else:
                stats["unmatched"] += 1
                logger.warning("No match for: %s", row.get(name_col, row.get(url_col, "?")))

    conn.commit()
    logger.info(
        "Import complete: %d matched, %d unmatched, %d skipped (of %d total)",
        stats["matched"], stats["unmatched"], stats["skipped"], stats["total"],
    )
    return stats


def main():
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(name)s — %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python -m nadia_ai.import_phantombuster <path_to_results.csv>")
        sys.exit(1)

    csv_path = Path(sys.argv[1])
    if not csv_path.exists():
        print(f"File not found: {csv_path}")
        sys.exit(1)

    conn = get_connection()
    stats = import_results(conn, csv_path)
    conn.close()

    print(f"Results: {stats}")


if __name__ == "__main__":
    main()
