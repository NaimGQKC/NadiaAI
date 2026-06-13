# Smoke Testing — verify scrapers at small scale (read me first)

**For the next Claude session / dev:** before running the full ~23-minute pipeline
(`python -m nadia_ai`), use the smoke-test harness to check any individual source
in seconds. It calls one scraper in isolation, prints the record count + a sample,
and **never touches `nadia_ai.db`** or runs Catastro/Ollama/delivery.

This exists because six scrapers (`boa, bop, borme, cee, ite, rememori`) silently
produced **0 records for weeks** and nobody noticed — the failure was buried in the
full-run logs. The harness surfaces a dead source instantly.

## Usage

```bash
python tools/smoke_test.py                  # run EVERY scraper
python tools/smoke_test.py boa bop          # run only these
python tools/smoke_test.py --days 30 boe    # change the lookback window
python tools/smoke_test.py --sample rememori  # also dump the first record
python tools/smoke_test.py --list           # list known scraper keys
```

- **Exit code** is non-zero if any requested scraper raised or returned 0 records,
  so it gates a pre-run check or CI.
- `[PASS]` = returned >0 records. `[FAIL]` = 0 records or raised (note shows why).
- Output is forced to loss-tolerant UTF-8, so it won't crash on emoji / accents
  (a real bug: traspaso titles contain 🍕 and the prod PhantomBuster export died on it).

## Reading the result

```
  [PASS] tablon           8 records    1.1s | sample=EdictRecord(...)
  [PASS] boe            501 records  112.7s
  [FAIL] borme            0 records    3.2s
```

- **PASS with records** → source is alive and parsing. Good.
- **FAIL, 0 records, fast** → fetch worked but parse/filter rejected everything
  (check the parser and `src/nadia_ai/utils/names.py`, which can over-reject names).
- **FAIL, RAISED** → fetch/endpoint problem (timeout, 404, moved URL). Note shows the
  exception.

## Keeping it in sync

The `SCRAPERS` registry in `tools/smoke_test.py` mirrors `scrapers_config` in
`src/nadia_ai/run.py`. **If you add/rename/re-signature a scraper in run.py, update
the harness registry too** (same keys, same kwargs).

`boe` uses `days=10` and the others use `since=<cutoff>`; `borme/cee/ite` take no
args; `traspasos` takes `zaragoza_only`. The harness encodes these per-scraper.

## Scope / limitations

- Tests the **scrape → records** stage only. It does **not** exercise merge, dedup,
  tier classification, Catastro RC enrichment, Ollama heir extraction, or delivery.
  For those, run the relevant module directly against a **copy** of the DB
  (`cp nadia_ai.db /tmp/test.db`), never the production file.
- A `[PASS]` means records were returned, not that every field is clean. Spot-check
  the `--sample` output (e.g. `causante='D'` from tablon is a known junk-name bug).

## Known-zero sources (history)

As of 2026-06-13, these were the all-time-zero scrapers under active repair:
`boa` (fetched fine, parse dropped all), `bop` (bop.dpz.es:80 timeout),
`borme` (silent 0), `cee` (JSF AJAX partial-response not parsed),
`ite` (zaragoza.es JSON endpoints 404), `rememori` (HTML selectors stale).
Re-run `python tools/smoke_test.py boa bop borme cee ite rememori` to check current state.
