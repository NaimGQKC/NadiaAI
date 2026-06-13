"""Smoke-test harness — run any/all scrapers IN ISOLATION, fast, no DB writes.

Why this exists
---------------
The full pipeline (`python -m nadia_ai`) takes ~23 minutes, hits the DB, runs
Catastro/Ollama/delivery, and hides which source is actually broken behind a
wall of logs. Six scrapers silently produced 0 records for weeks before anyone
noticed. This harness calls one scraper at a time, with a short lookback window,
prints how many records it returned + a sample, and NEVER touches nadia_ai.db.

Use it to answer "is source X alive and parsing?" in seconds, not minutes.

Usage
-----
    python tools/smoke_test.py                 # run every scraper
    python tools/smoke_test.py boa bop          # run only these
    python tools/smoke_test.py --days 30 boe    # widen/narrow the lookback
    python tools/smoke_test.py --sample boa      # also dump the first record

Exit code is non-zero if any requested scraper raised or returned 0 records,
so it can gate CI / a pre-run check.
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import UTC, datetime, timedelta

# Windows consoles default to cp1252, which crashes on emoji/non-Latin chars
# present in some sources (e.g. traspasos business titles with 🍕). Force a
# loss-tolerant UTF-8 stdout so the harness can never die on its own output.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
except (AttributeError, ValueError):
    pass

# Keep this registry in sync with scrapers_config in src/nadia_ai/run.py.
# Each entry: key -> (module_path, function_name, kwargs_builder(cutoff) -> dict)
SCRAPERS: dict[str, tuple[str, str, object]] = {
    "tablon":      ("nadia_ai.scrapers.tablon",      "scrape_tablon",      lambda c: {"since": c}),
    "boa":         ("nadia_ai.scrapers.boa",         "scrape_boa",         lambda c: {"since": c}),
    "bop":         ("nadia_ai.scrapers.bop",         "scrape_bop",         lambda c: {"since": c}),
    "boe":         ("nadia_ai.scrapers.boe",         "scrape_boe",         lambda c: {"days": 10}),
    "borme":       ("nadia_ai.scrapers.borme",       "scrape_borme",       lambda c: {}),
    "esquelas":    ("nadia_ai.scrapers.esquelas",    "scrape_esquelas",    lambda c: {"since": c}),
    "defunciones": ("nadia_ai.scrapers.defunciones", "scrape_defunciones", lambda c: {"since": c}),
    "rememori":    ("nadia_ai.scrapers.rememori",    "scrape_rememori",    lambda c: {"since": c}),
    "iesquelas":   ("nadia_ai.scrapers.iesquelas",   "scrape_iesquelas",   lambda c: {"since": c}),
    "cee":         ("nadia_ai.scrapers.cee",         "scrape_cee",         lambda c: {}),
    "ite":         ("nadia_ai.scrapers.ite",         "scrape_ite",         lambda c: {}),
    "traspasos":   ("nadia_ai.scrapers.traspasos",   "scrape_traspasos",   lambda c: {"zaragoza_only": False}),
}


def run_one(key: str, cutoff: datetime, show_sample: bool) -> tuple[bool, int, str]:
    """Call a single scraper in isolation. Returns (ok, count, note)."""
    mod_path, func_name, kwargs_builder = SCRAPERS[key]
    kwargs = kwargs_builder(cutoff)
    try:
        module = __import__(mod_path, fromlist=[func_name])
        func = getattr(module, func_name)
    except Exception as e:  # import/registration error
        return False, 0, f"IMPORT ERROR: {e}"

    t0 = time.monotonic()
    try:
        records = func(**kwargs)
    except Exception as e:
        tb = traceback.format_exc().strip().splitlines()[-1]
        return False, 0, f"RAISED after {time.monotonic() - t0:.1f}s: {tb}"
    elapsed = time.monotonic() - t0

    records = list(records or [])
    n = len(records)
    note = f"{elapsed:5.1f}s"
    if show_sample and records:
        first = records[0]
        if isinstance(first, dict):
            note += f" | keys={sorted(first.keys())}\n         sample={ {k: first[k] for k in list(first)[:6]} }"
        else:
            note += f" | sample={first!r}"
    # 0 records is a soft failure: the scraper ran but produced nothing.
    ok = n > 0
    return ok, n, note


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scrapers in isolation (no DB writes).")
    parser.add_argument("scrapers", nargs="*", help="scraper keys to run (default: all)")
    parser.add_argument("--days", type=int, default=90, help="lookback window in days (default 90)")
    parser.add_argument("--sample", action="store_true", help="print the first record of each scraper")
    parser.add_argument("--list", action="store_true", help="list known scraper keys and exit")
    args = parser.parse_args()

    if args.list:
        print("Known scrapers:", ", ".join(SCRAPERS))
        return 0

    keys = args.scrapers or list(SCRAPERS)
    unknown = [k for k in keys if k not in SCRAPERS]
    if unknown:
        print(f"Unknown scraper(s): {unknown}\nKnown: {', '.join(SCRAPERS)}")
        return 2

    cutoff = datetime.now(UTC) - timedelta(days=args.days)
    print(f"Smoke test — lookback {args.days}d (cutoff {cutoff.date()}), {len(keys)} scraper(s)\n")

    results: list[tuple[str, bool, int, str]] = []
    for key in keys:
        ok, n, note = run_one(key, cutoff, args.sample)
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {key:<12} {n:>5} records  {note}")
        results.append((key, ok, n, note))

    failed = [k for k, ok, _, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} scrapers returned >0 records.")
    if failed:
        print("ZERO/ERROR:", ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
