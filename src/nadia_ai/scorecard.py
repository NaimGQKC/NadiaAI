"""Per-comunidad actionability scorecard, logged on every pipeline run.

The end product is an *actionable* inheritance lead = named heir + a mailable
property address (street number or referencia catastral) + an open window. Raw
volume hides whether a territory is actually sellable, so this measures the funnel
per comunidad autónoma so we can see exactly where the gap is (usually: address):

    total · TierA · %heir · %address(street) · %RC · %ready

`ready` = Tier A/B with a named heir AND a contact path (street address or phone) =
sellable today. Written to the run log and to logs/scorecard_<date>.json for tracking.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from nadia_ai.utils.regions import ccaa_for

logger = logging.getLogger("nadia_ai.scorecard")

_FIELDS = ("total", "A", "B", "heir", "address", "rc", "ready")


def _has_digit(s: str | None) -> bool:
    return bool(s) and any(ch.isdigit() for ch in s)


def _blank() -> dict:
    return {f: 0 for f in _FIELDS}


def compute_scorecard(conn: sqlite3.Connection) -> dict:
    """Compute the actionability funnel overall and per comunidad autónoma."""
    overall = _blank()
    by_ccaa: dict[str, dict] = {}
    rows = conn.execute(
        "SELECT tier, region, localidad, direccion, referencia_catastral, "
        "heir_name, contact_phone FROM leads"
    )
    for tier, region, localidad, direccion, rc, heir, phone in rows:
        ccaa = ccaa_for(region, localidad, direccion)
        bucket = by_ccaa.setdefault(ccaa, _blank())
        t = (tier or "").upper()
        has_heir = bool((heir or "").strip())
        has_addr = _has_digit(direccion)
        has_rc = bool((rc or "").strip())
        ready = t in ("A", "B") and has_heir and (has_addr or bool((phone or "").strip()))
        for c in (overall, bucket):
            c["total"] += 1
            if t == "A":
                c["A"] += 1
            elif t == "B":
                c["B"] += 1
            if has_heir:
                c["heir"] += 1
            if has_addr:
                c["address"] += 1
            if has_rc:
                c["rc"] += 1
            if ready:
                c["ready"] += 1
    return {"overall": overall, "by_ccaa": by_ccaa}


def _pct(n: int, total: int) -> int:
    return round(100 * n / total) if total else 0


def log_scorecard(conn: sqlite3.Connection, logs_dir: Path | None = None) -> dict:
    """Compute, log (one line per comunidad, sorted by Tier A+B) and persist the
    scorecard. Returns the computed dict. Never raises on the persist step."""
    sc = compute_scorecard(conn)
    o = sc["overall"]
    logger.info(
        "Scorecard OVERALL: total=%d A=%d B=%d | heir=%d%% address=%d%% rc=%d%% ready=%d%%",
        o["total"], o["A"], o["B"], _pct(o["heir"], o["total"]),
        _pct(o["address"], o["total"]), _pct(o["rc"], o["total"]),
        _pct(o["ready"], o["total"]),
    )
    for ccaa in sorted(sc["by_ccaa"], key=lambda c: sc["by_ccaa"][c]["A"] + sc["by_ccaa"][c]["B"],
                       reverse=True):
        c = sc["by_ccaa"][ccaa]
        logger.info(
            "Scorecard %s: A=%d B=%d ready=%d (heir %d%%, addr %d%%, rc %d%%)",
            ccaa, c["A"], c["B"], c["ready"], _pct(c["heir"], c["total"]),
            _pct(c["address"], c["total"]), _pct(c["rc"], c["total"]),
        )
    try:
        logs_dir = logs_dir or Path("logs")
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y-%m-%d")
        (logs_dir / f"scorecard_{stamp}.json").write_text(
            json.dumps(sc, ensure_ascii=False, indent=2)
        )
    except Exception as e:
        logger.warning("Scorecard persist failed (non-critical): %s", e)
    return sc
