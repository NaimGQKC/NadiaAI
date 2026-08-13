"""Clear the whole heir-extraction backlog — no 200-lead cap, no time budget.

The daily pipeline extracts at most 200 leads per run inside an 18-minute budget it
shares with every other step, while scraping adds several hundred to a couple of
thousand. Intake therefore outruns extraction and the pending pile grows. This tool
exists to drain it in one go: every pending lead, high parallelism, no deadline.

Order is deliberate — Zaragoza first (the client's market), then the rest of Aragón,
then everywhere else — so the leads that matter most are named even if a later phase
is interrupted.

Costs LLM tokens (the extraction model), never PDL credits.

Env:
    EXTRACT_SCOPE     "zaragoza" | "aragon" | "all"  (default "all" = all three phases)
    EXTRACTION_WORKERS  parallel workers (default 12 here; the pipeline default is 6)
    EXTRACT_MAX       safety cap on leads per phase (default 100000 = no real cap)
    EXTRACT_DRY       "1" = report the pending counts per phase and exit, 0 tokens
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nadia_ai.config import DB_PATH  # noqa: E402

# Parallelism: the work is network-bound (fetch the source doc + call the LLM), so more
# workers is nearly linear — but each one also hits a Spanish government site, and BOE
# already blocks aggressive clients. 12 is a deliberate middle ground; raise with care.
os.environ.setdefault("EXTRACTION_WORKERS", "12")

DRY = os.getenv("EXTRACT_DRY") == "1"
SCOPE = (os.getenv("EXTRACT_SCOPE") or "all").strip().lower()
MAX_PER_PHASE = int(os.getenv("EXTRACT_MAX", "100000"))

_ZGZ = "(region LIKE '%aragoza%' OR localidad LIKE '%aragoza%')"
_ARA = ("(region LIKE '%aragoza%' OR localidad LIKE '%aragoza%'"
        " OR region LIKE '%uesca%' OR localidad LIKE '%uesca%'"
        " OR region LIKE '%eruel%' OR localidad LIKE '%eruel%')")

# (label, extra SQL). Each phase only takes leads still pending, so phases never overlap.
_PHASES = [
    ("Zaragoza", _ZGZ),
    ("rest of Aragón", _ARA),
    ("rest of Spain", ""),
]


def _setup_logging() -> None:
    try:
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s",
                        datefmt="%H:%M:%S", stream=sys.stdout, force=True)
    for name in ("nadia_ai", "nadia_ai.utils.extraction", "nadia_ai.catastro"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.INFO)
        lg.propagate = True


_setup_logging()
log = logging.getLogger("backlog")


def _pending(conn: sqlite3.Connection, extra: str) -> int:
    q = "SELECT COUNT(*) FROM leads WHERE ai_extraction_done = 0"
    if extra:
        q += f" AND {extra}"
    return conn.execute(q).fetchone()[0]


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    bar = "=" * 70
    print(bar)
    print("  HEIR EXTRACTION BACKLOG  ·  no 200-cap, no time budget")
    print(f"  db: {DB_PATH}   workers: {os.environ['EXTRACTION_WORKERS']}"
          f"   mode: {'DRY' if DRY else 'LIVE'}")
    print(bar)

    total_pending = _pending(conn, "")
    log.info("Pending overall: %d", total_pending)

    phases = _PHASES
    if SCOPE == "zaragoza":
        phases = _PHASES[:1]
    elif SCOPE == "aragon":
        phases = _PHASES[:2]

    for label, extra in phases:
        log.info("Phase '%s' · pending here: %d", label, _pending(conn, extra))
    if DRY:
        log.info("[DRY] no extraction run, 0 tokens spent.")
        return 0

    from nadia_ai.utils.extraction import run_heir_extraction

    _setup_logging()  # re-assert after the import reconfigures logging
    t0 = time.monotonic()
    done_total = 0

    for label, extra in phases:
        n = min(_pending(conn, extra), MAX_PER_PHASE)
        if not n:
            log.info("Phase '%s' · nothing pending, skipping.", label)
            continue
        log.info("Phase '%s' · extracting %d leads (no deadline)…", label, n)
        t1 = time.monotonic()
        try:
            # deadline=None: this tool exists precisely to NOT stop early.
            done = run_heir_extraction(conn, limit=n, extra_where=extra, deadline=None)
            done_total += done
            log.info("Phase '%s' · DONE — %d extracted in %.0fs (%d left pending here)",
                     label, done, time.monotonic() - t1, _pending(conn, extra))
        except Exception as e:
            log.error("Phase '%s' FAILED (%s) — continuing to the next phase.", label, e)

    print(bar)
    log.info("BACKLOG RUN COMPLETE · %d leads extracted in %.0fs · %d still pending overall",
             done_total, time.monotonic() - t0, _pending(conn, ""))
    print(bar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
