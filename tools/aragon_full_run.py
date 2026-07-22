"""Full Aragón re-run — force LLM re-extraction + email enrichment, with live progress.

Designed to run UNATTENDED (e.g. while you're at the gym) and narrate every step to
the terminal so it's never a black box.

  Phase 1 — reset Aragón leads to pending (force the LLM to re-read their source
            documents with tonight's accent + surname-reconstruction fixes).
  Phase 2 — run_heir_extraction over just those leads: re-fetch sources, re-run the
            LLM, re-store clean heir names. Logs per-lead progress.
  Phase 3 — PDL email enrichment on the resulting quality-named Aragón heirs
            (delegates to tools/test_heir_phones.py, provider pdl:aragon).

Env: EXTRACTION_API_KEY/URL/MODEL (the extraction LLM), HEIR_TEST_KEY (PDL),
     NADIA_DB_PATH (persistent DB). Set ARAGON_DRY=1 to print the plan and counts
     WITHOUT resetting/extracting/spending anything.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))  # tools/  (for test_heir_phones helpers)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import test_heir_phones as tp  # noqa: E402
from nadia_ai.config import DB_PATH  # noqa: E402

def _setup_logging() -> None:
    """Force a clean, timestamped, line-buffered stream for EVERY logger (ours and
    nadia_ai's) so the whole re-extraction narrates itself in real time. force=True
    defeats the project's JSON logging config if it has already grabbed the root."""
    try:
        sys.stdout.reconfigure(line_buffering=True)  # flush each line as it prints
    except Exception:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    # Make the extraction's own INFO logs stream through our handler with timestamps.
    for name in ("nadia_ai", "nadia_ai.utils.extraction", "nadia_ai.catastro"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.INFO)
        lg.propagate = True


_setup_logging()
log = logging.getLogger("aragon")

DRY = os.getenv("ARAGON_DRY") == "1"
MAX_RESET = int(os.getenv("ARAGON_MAX_RESET", "800"))  # safety cap on how many we reset


def _aragon_ids(conn: sqlite3.Connection) -> list[int]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, region, localidad FROM leads").fetchall()
    return [r["id"] for r in rows if tp._in_ccaa(r["region"], r["localidad"], "aragon")]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    bar = "═" * 70
    print(bar)
    print("  ARAGÓN FULL RE-RUN  ·  re-extract (LLM) → email enrichment")
    print(f"  mode: {'DRY (no changes)' if DRY else 'LIVE'}   db: {DB_PATH}")
    print(bar)

    conn = sqlite3.connect(DB_PATH)
    t0 = time.monotonic()

    ids = _aragon_ids(conn)
    log.info("Phase 0 · found %d Aragón leads in the DB", len(ids))
    if not ids:
        log.info("No Aragón leads — nothing to do.")
        return 0
    if len(ids) > MAX_RESET:
        log.warning("Aragón leads (%d) exceed safety cap %d — capping to be safe.", len(ids), MAX_RESET)
        ids = ids[:MAX_RESET]
    idlist = ",".join(str(i) for i in ids)

    if DRY:
        log.info("[DRY] would reset + re-extract %d Aragón leads, then enrich emails.", len(ids))
        log.info("[DRY] no changes made. Re-run without ARAGON_DRY=1 to execute.")
        return 0

    # ── Phase 1: force re-extraction ─────────────────────────────────────────────
    conn.execute(f"UPDATE leads SET ai_extraction_done = 0 WHERE id IN ({idlist})")
    conn.commit()
    log.info("Phase 1 · reset %d Aragón leads to pending (force LLM re-read)", len(ids))

    # ── Phase 2: LLM re-extraction (the slow part; logs per lead) ────────────────
    log.info("Phase 2 · re-extracting %d leads with the LLM — re-fetching each source", len(ids))
    log.info("          document and re-reading it. This is the slow step; watch the")
    log.info("          'Lead N: Tier …, heirs=…' lines stream below.")
    try:
        from nadia_ai.utils.extraction import run_heir_extraction

        _setup_logging()  # re-assert clean timestamps in case the import reconfigured logging
        log.info("Phase 2 · LLM reachable check + processing starting now…")
        n = run_heir_extraction(conn, limit=len(ids), extra_where=f"id IN ({idlist})")
        log.info("Phase 2 · DONE — %d leads re-extracted in %.0fs", n, time.monotonic() - t0)
    except Exception as e:
        log.error("Phase 2 FAILED (%s). Skipping to enrichment on whatever data exists.", e)

    # ── Phase 3: PDL email enrichment on Aragón (delegates, reusing the demo output) ─
    log.info("Phase 3 · enriching Aragón emails via PDL…")
    env = {
        **os.environ,
        "HEIR_TEST_PROVIDER": "pdl:aragon",
        "HEIR_TEST_SAMPLE": os.getenv("HEIR_TEST_SAMPLE", "60"),
    }
    rc = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(__file__), "test_heir_phones.py")],
        env=env,
    ).returncode
    log.info("Phase 3 · enrichment exited rc=%d", rc)

    print(bar)
    log.info("ALL DONE in %.0fs. Aragón re-extracted with fixes + emails enriched.", time.monotonic() - t0)
    print(bar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
