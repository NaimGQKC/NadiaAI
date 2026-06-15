"""Record a conversion outcome against an outreach log entry.

Run this the moment the outcome of a contact attempt is known — the funnel
analytics in ``nadia_ai.outreach_log.funnel()`` depend on these records.
If the outcome is ``opted_out`` the person is also added to the suppression
list automatically so they can never reappear in a future worklist.

Usage::

    python -m tools.mark_outcome --lead-id 42 --outcome interested
    python -m tools.mark_outcome --lead-id 17 --outcome opted_out --notes "pidió no ser contactado"
    python -m tools.mark_outcome --list
    python -m tools.mark_outcome --funnel

Valid outcomes: pending, no_answer, not_interested, interested, listed, opted_out
"""

from __future__ import annotations

import argparse
import sqlite3

from nadia_ai.outreach_log import (
    VALID_OUTCOMES,
    funnel,
    init_outreach_log_schema,
    record_outcome,
)

DB = "nadia_ai.db"


def _print_funnel(conn: sqlite3.Connection) -> None:
    f = funnel(conn)
    print("--- Conversion funnel ---")
    print(f"  Contacted  : {f['contacted']}")
    print(f"  Responded  : {f['responded']}  ({f['response_rate']:.1%})")
    print(f"  Interested : {f['interested']}  ({f['interest_rate']:.1%} of responded)")
    print(f"  Listed     : {f['listed']}  ({f['listing_rate']:.1%} of interested)")
    print(f"  Opted out  : {f['opted_out']}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Record a conversion outcome for a lead's outreach entry."
    )
    ap.add_argument(
        "--lead-id", type=int,
        help="leads.id of the lead whose outcome you are recording",
    )
    ap.add_argument(
        "--outcome",
        choices=sorted(VALID_OUTCOMES),
        help="Conversion outcome to record",
    )
    ap.add_argument(
        "--notes", default="",
        help="Optional free-text note (e.g. 'llamó de vuelta, cita el martes')",
    )
    ap.add_argument(
        "--list", action="store_true",
        help="List recent outreach log entries",
    )
    ap.add_argument(
        "--funnel", action="store_true",
        help="Print conversion funnel stats",
    )
    ap.add_argument("--db", default=DB, help="Path to SQLite database (default: nadia_ai.db)")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    init_outreach_log_schema(conn)

    if args.funnel:
        _print_funnel(conn)
        conn.close()
        return

    if args.list:
        rows = conn.execute(
            """
            SELECT id, lead_id, channel, tipo, outcome, created_at, outcome_at, notes
            FROM outreach_log
            ORDER BY created_at DESC
            LIMIT 50
            """
        ).fetchall()
        print(f"{len(rows)} most-recent outreach log entries (max 50):")
        for r in rows:
            print(
                f"  id={r['id']} lead_id={r['lead_id']} channel={r['channel']} "
                f"tipo={r['tipo']} outcome={r['outcome']} sent={r['created_at'][:10]} "
                f"notes={r['notes']!r}"
            )
        conn.close()
        return

    if not args.lead_id or not args.outcome:
        ap.error("--lead-id and --outcome are both required (or use --list / --funnel)")

    updated = record_outcome(conn, args.lead_id, args.outcome, notes=args.notes)
    conn.close()

    if updated:
        print(
            f"Outcome '{args.outcome}' recorded for lead_id={args.lead_id}."
            + (" (added to suppression list)" if args.outcome == "opted_out" else "")
        )
    else:
        print(
            f"No outreach row found for lead_id={args.lead_id} — "
            "run log_outreach first."
        )


if __name__ == "__main__":
    main()
