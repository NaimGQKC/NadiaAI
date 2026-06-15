"""Add, list, and manage tenants (real-estate agent accounts).

Each tenant is scoped to one territory (one or more provinces / localidades).
Their worklist is automatically limited to leads in that territory — they never
see leads from outside their area.

Usage:
    python -m tools.tenant --add --name "Ana Muro" --agency "RE/MAX" \\
        --phone "600123456" --territory "Zaragoza,Huesca"

    python -m tools.tenant --list

    python -m tools.tenant --list --active-only

    python -m tools.tenant --set-territory 3 --territory "Zaragoza,Calatayud,Ejea"

    python -m tools.tenant --disable 3
    python -m tools.tenant --enable 3
"""

from __future__ import annotations

import argparse
import json
import sqlite3

from nadia_ai.tenancy import (
    add_tenant,
    init_tenancy_schema,
    list_tenants,
    set_tenant_active,
    update_territory,
)

DB = "nadia_ai.db"


def _parse_territory(raw: str) -> list[str]:
    """Split a comma-separated territory string into a cleaned list."""
    return [t.strip() for t in raw.split(",") if t.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description="Manage NadiaAI agent tenants.")
    ap.add_argument("--db", default=DB, metavar="PATH", help="SQLite DB path")

    action = ap.add_mutually_exclusive_group(required=True)
    action.add_argument("--add", action="store_true", help="Add a new tenant")
    action.add_argument("--list", action="store_true", help="List tenants")
    action.add_argument(
        "--set-territory",
        type=int,
        metavar="ID",
        help="Replace the territory for tenant ID",
    )
    action.add_argument("--disable", type=int, metavar="ID", help="Disable tenant ID")
    action.add_argument("--enable", type=int, metavar="ID", help="Enable tenant ID")

    ap.add_argument("--name", help="Agent display name (required for --add)")
    ap.add_argument("--agency", default="", help="Agency name")
    ap.add_argument("--phone", default="", help="Agent phone")
    ap.add_argument(
        "--territory",
        default="",
        help="Comma-separated list of provinces / localidades, e.g. 'Zaragoza,Huesca'",
    )
    ap.add_argument(
        "--active-only", action="store_true", help="With --list: show only active tenants"
    )

    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    init_tenancy_schema(conn)

    # ── Add ───────────────────────────────────────────────────────────────────
    if args.add:
        if not args.name:
            ap.error("--add requires --name")
        territory = _parse_territory(args.territory) if args.territory else []
        tid = add_tenant(
            conn,
            name=args.name,
            agency=args.agency,
            phone=args.phone,
            territory=territory,
        )
        print(f"Tenant added — id={tid}  name={args.name!r}  territory={territory}")
        conn.close()
        return

    # ── List ─────────────────────────────────────────────────────────────────
    if args.list:
        tenants = list_tenants(conn, active_only=args.active_only)
        label = "active tenants" if args.active_only else "tenants"
        print(f"{len(tenants)} {label}:")
        for t in tenants:
            status = "active" if t["active"] else "disabled"
            terr = json.dumps(t["territory_json"], ensure_ascii=False)
            print(
                f"  id={t['id']}  {t['name']!r}  {t['agency'] or '—'}"
                f"  tel={t['phone'] or '—'}  [{status}]  territory={terr}"
                f"  created={t['created_at'][:10]}"
            )
        conn.close()
        return

    # ── Set territory ─────────────────────────────────────────────────────────
    if args.set_territory is not None:
        if not args.territory:
            ap.error("--set-territory requires --territory")
        territory = _parse_territory(args.territory)
        ok = update_territory(conn, args.set_territory, territory)
        if ok:
            print(f"Tenant {args.set_territory}: territory updated to {territory}")
        else:
            print(f"Tenant id={args.set_territory} not found.")
        conn.close()
        return

    # ── Disable / Enable ──────────────────────────────────────────────────────
    target_id = args.disable if args.disable is not None else args.enable
    active = args.enable is not None
    ok = set_tenant_active(conn, target_id, active)
    state = "enabled" if active else "disabled"
    if ok:
        print(f"Tenant {target_id}: {state}.")
    else:
        print(f"Tenant id={target_id} not found.")
    conn.close()


if __name__ == "__main__":
    main()
