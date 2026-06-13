"""One-off repair: backfill date_of_death and clean malformed causante.

Run once against nadia_ai.db to fix existing rows created before these
fixes landed. Safe to run multiple times (idempotent).

BUG 1 fix: Defunciones leads missing date_of_death — backfill from the
  linked edict's published_at (which the scraper sets to datetime.now(UTC)
  at scrape time, a reasonable proxy for death date).

BUG 2 fix: null out causante values that fail is_valid_person_name for
  non-B2B sources (Traspasos/BORME are exempt).
"""

import sqlite3
import sys
from pathlib import Path

# Resolve DB path relative to repo root
REPO_ROOT = Path(__file__).parent.parent
DB_PATH = REPO_ROOT / "nadia_ai.db"

sys.path.insert(0, str(REPO_ROOT / "src"))
from nadia_ai.utils.names import is_valid_person_name  # noqa: E402

B2B_SUBSOURCES = {"Traspasos", "BORME-I", "BORME-II"}
OBITUARY_SOURCES = {"Defunciones", "Esquelas", "iEsquelas", "Rememori"}


def fix_missing_date_of_death(conn: sqlite3.Connection) -> int:
    """Backfill date_of_death for obituary leads that have edict published_at.

    Approach: for each Defunciones/obituary lead with no date_of_death,
    look up its linked edict's published_at and use that date (date part only).
    """
    rows = conn.execute(
        """
        SELECT l.id, e.published_at
        FROM leads l
        JOIN lead_edicts le ON le.lead_id = l.id
        JOIN edicts e ON e.id = le.edict_id
        WHERE l.subsource IN ('Defunciones', 'Esquelas', 'iEsquelas', 'Rememori')
          AND (l.date_of_death IS NULL OR l.date_of_death = '')
          AND e.published_at IS NOT NULL
          AND e.published_at != ''
        """
    ).fetchall()

    fixed = 0
    for lead_id, published_at in rows:
        # published_at may be ISO string: "2026-05-13T20:35:23.369557+00:00"
        date_part = published_at[:10]  # "YYYY-MM-DD"
        conn.execute(
            "UPDATE leads SET date_of_death = ? WHERE id = ? AND (date_of_death IS NULL OR date_of_death = '')",
            (date_part, lead_id),
        )
        fixed += 1

    conn.commit()
    return fixed


def fix_malformed_causante(conn: sqlite3.Connection) -> int:
    """Null out causante values that fail person-name validation.

    B2B sources (Traspasos, BORME-I, BORME-II) are exempt — their
    causante is a legitimate company/entity name.
    """
    rows = conn.execute(
        "SELECT id, causante, subsource FROM leads WHERE causante IS NOT NULL AND causante != ''"
    ).fetchall()

    fixed = 0
    for lead_id, causante, subsource in rows:
        if subsource in B2B_SUBSOURCES:
            continue
        if not is_valid_person_name(causante):
            conn.execute(
                "UPDATE leads SET causante = '', causante_norm = NULL WHERE id = ?",
                (lead_id,),
            )
            fixed += 1

    conn.commit()
    return fixed


def main() -> None:
    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    print(f"Connected to {DB_PATH}")

    # BUG 1: backfill date_of_death
    n1 = fix_missing_date_of_death(conn)
    print(f"[BUG 1] Backfilled date_of_death for {n1} obituary leads")

    # BUG 2: clean malformed causante
    n2 = fix_malformed_causante(conn)
    print(f"[BUG 2] Nulled {n2} malformed causante values")

    # Summary counts after repair
    total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    still_missing_dod = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE (date_of_death IS NULL OR date_of_death = '') AND subsource IN ('Defunciones','Esquelas','iEsquelas','Rememori')"
    ).fetchone()[0]
    print(f"\nPost-repair: {total} total leads, {still_missing_dod} obituary leads still missing date_of_death")

    conn.close()


if __name__ == "__main__":
    main()
