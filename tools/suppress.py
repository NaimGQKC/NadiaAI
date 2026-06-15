"""Record a do-not-contact / opt-out request (GDPR right to object, LSSI opt-out).

Run this the moment someone asks not to be contacted — the next outreach pack will
exclude them automatically (see nadia_ai.suppression). At least one of
--name/--phone/--email is required.

Usage:
    python -m tools.suppress --name "Juan Pérez García" --reason "pidió no ser contactado"
    python -m tools.suppress --phone "+34 976 12 34 56" --reason "queja telefónica"
    python -m tools.suppress --list
"""

from __future__ import annotations

import argparse
import sqlite3

from nadia_ai.suppression import add_suppression, init_suppression_schema

DB = "nadia_ai.db"


def main() -> None:
    ap = argparse.ArgumentParser(description="Add or list do-not-contact entries.")
    ap.add_argument("--name")
    ap.add_argument("--phone")
    ap.add_argument("--email")
    ap.add_argument("--reason", default="")
    ap.add_argument("--source", default="manual")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--list", action="store_true", help="List current suppression entries")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    init_suppression_schema(conn)

    if args.list:
        rows = conn.execute(
            "SELECT id, name_norm, phone, email, reason, source, added_at "
            "FROM suppression_list ORDER BY added_at DESC"
        ).fetchall()
        print(f"{len(rows)} suppression entries:")
        for r in rows:
            print(" ", dict(zip(
                ["id", "name", "phone", "email", "reason", "source", "added_at"], r
            )))
        conn.close()
        return

    if not (args.name or args.phone or args.email):
        ap.error("provide at least one of --name / --phone / --email (or --list)")

    added = add_suppression(
        conn, name=args.name, phone=args.phone, email=args.email,
        reason=args.reason, source=args.source,
    )
    conn.close()
    print("Added to suppression list." if added else "Already present — no change.")


if __name__ == "__main__":
    main()
