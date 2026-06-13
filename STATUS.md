# NadiaAI — Phase 2 Build Sprint Status

**Sprint start:** 2026-04-30T10:00:00+02:00
**Target:** EOD 2026-04-30
**Status:** READY — all Priority 0 + 1 shipped, Priority 2 shipped, 110 tests passing

## Priority 0 — Pipeline Correctness
| Item | Status | Notes |
|------|--------|-------|
| 0.1 first_seen_at daily delta | DONE | merge.py `first_seen_at`, delivery only shows daily delta |
| 0.2 Cross-source dedup | DONE | RC > name > address matching, sources merged |
| 0.3 Tier classification | DONE | A/B/C/X with staleness rule (>6mo = downgrade B) |
| 0.4 Outreach-legality flag | DONE | outreach_allowed + outreach_notes, distress = blocked |
| 0.5 BOA parser polish | DONE | Added 6 new regex patterns for name/address/location |

## Priority 1 — New Sources
| Item | Status | Notes |
|------|--------|-------|
| 1.1 BOE TEJU + Sec.V | DONE | scrapers/boe.py — TEJU HTML search + Sec.V XML API |
| 1.2 BORME (Secciones I+II) | DONE | scrapers/borme.py — BOE XML API, death/dissolution filter |
| 1.3 INE Calibration | DONE | ine.py + weekly-ine.yml cron + Calibracion tab writer |

## Priority 2 — Enrichment
| Item | Status | Notes |
|------|--------|-------|
| 2.1 BOPZ (BOP Zaragoza) | DONE | scrapers/bop.py — HTML scraping of bop.dpz.es |
| 2.2 Subastas BOE enrichment | DONE | enrichment.py — JV+NV, cross-join by RC/address |
| 2.3 Zaragoza Open Data obras | DONE | enrichment.py — licencias de obra, cross-join by address |

## Priority 3 — Rejected (documented)
| Item | Status | Notes |
|------|--------|-------|
| 3.1 Publicidad Concursal | REJECTED | docs/research/concursal-rejected.md |
| 3.2 BOE Seccion II.B | REJECTED | docs/research/seccion-IIB-rejected.md |

## Test Results
- **110 tests passing** (unit tests for all modules)
- Covers: models, DB, merge/dedup, tier classification, outreach flags, daily delta, delivery, parsers (Tablon, BOA, BOE), pipeline orchestration, special characters
- All QA adversarial cases verified:
  - Same edict on 2 days → no duplicate row
  - Same death in Tablon + BOA → 1 merged row with both sources
  - Name only → Tier B; name+address → Tier A; neither → Tier C
  - Subasta/concurso → Tier X, outreach_allowed=false
  - Unicode (n, c, accents) round-trips through SQLite and merge
  - All scrapers failing → pipeline still completes

## Source Taxonomy
| Source | Code | Type | Volume | Outreach |
|--------|------|------|--------|----------|
| Tablon Edictos Zaragoza | tablon | Herencia individual | ~30-50/year | Yes |
| BOA Junta Distribuidora | boa | Herencia individual | ~5-15/year | Yes |
| BOE TEJU (judicial edicts) | boe_teju | Herencia individual | ~30-80/year | Yes |
| BOE Seccion V.B (state-as-heir) | boe_secv | Herencia individual | ~1-5/year | Yes |
| BORME Seccion I+II | borme_i / borme_ii | B2B (empresa) | ~3-8/month | Yes (B2B) |
| BOP Zaragoza | bop | Herencia individual | ~50-150/year | Yes |
| Esquelas Memora | esquelas | Obituario | High volume | Yes |
| Defunciones.es | defunciones | Obituario | Variable | Yes |
| iEsquelas.com | iesquelas | Obituario (agregador) | Variable | Yes |
| Solares Zaragoza | solares | Solar vacante | ~2400 total | Yes |
| Licencias Obra | licencias | Licencia edificación | Variable | Yes |
| Servihabitat | servihabitat | Inmueble bancario | ~200/batch | Yes |
| **CEE Aragón** | **cee** | **Certificado energético** | **Variable** | **Yes** |
| **Traspasos Aragón** | **traspasos** | **Traspaso B2B** | **~60 active** | **Yes (B2B)** |
| **ITE Zaragoza** | **ite** | **ITE desfavorable** | **Variable** | **Yes** |
| Subastas BOE | subastas | Enrichment only (context flag) | Variable | NO — removed as lead source 2026-06-12: client is a listing agent, auctions are investor/buy-side deals |
| Zaragoza Open Data Obras | obras | Enrichment only | Monthly batch | N/A |
| INE (calibration) | ine | Dashboard only | Weekly | N/A |

## Schema (Phase 2 additions)
- `tier` (A/B/C/X), `sources` (JSON array), `subsource`, `first_seen_at`
- `outreach_allowed` (bool), `outreach_notes` (string)
- `subasta_activa`, `obras_recientes` (enrichment cross-join)
- `nif`, `valor_tasacion`, `procedimiento` (BOE V.B / subastas)

## Files Added/Modified (Phase 2)
```
NEW:  src/nadia_ai/merge.py           — Dedup, tier, outreach engine
NEW:  src/nadia_ai/ine.py             — INE calibration data fetcher
NEW:  src/nadia_ai/enrichment.py      — Subastas + Obras enrichment
NEW:  src/nadia_ai/scrapers/boe.py    — BOE TEJU + Section V scraper
NEW:  src/nadia_ai/scrapers/bop.py    — BOP Zaragoza scraper
NEW:  src/nadia_ai/scrapers/borme.py  — BORME scraper
NEW:  .github/workflows/weekly-ine.yml
NEW:  tests/unit/test_merge.py        — 25 dedup/tier/outreach tests
```

## Files Added/Modified (Phase 3 — New Data Sources)
```
NEW:  src/nadia_ai/scrapers/cee.py        — CEE Aragón energy certificates (JSF form)
NEW:  src/nadia_ai/scrapers/traspasos.py  — Traspasos Aragón business transfers (WordPress)
NEW:  src/nadia_ai/scrapers/ite.py        — ITE Zaragoza building inspections (dual-attempt)
NEW:  src/nadia_ai/scrapers/subastas.py   — Subastas BOE elevated to primary lead gen
MOD:  src/nadia_ai/merge.py               — +3 source labels, ITE/traspasos outreach rules
MOD:  src/nadia_ai/run.py                 — +4 scraper steps (3k-3n), 15 total scrapers
MOD:  STATUS.md                            — Updated source taxonomy
```

## Data Quality Fixes (2026-06-12 PM)
| Item | Status | Notes |
|------|--------|-------|
| BOE TEJU rebuilt | DONE | Old title-filter found 0 of ~4,200 edicts/day (generic titles). Now uses BOE full-text search (`buscar/edictos_judiciales.php`) for "herencia yacente"/"herederos" → ~60-90 records/day with causante + localidad |
| BOE-N notarial added | DONE | Notarial "declaración de herederos" announcements (~12/day). Old code verified against a 404 URL and dropped 100%. Title carries causante + notary city; no body fetch needed. Source `boe_n` |
| Name validation layer | NEW | `utils/names.py` — `is_valid_person_name` / `clean_name_list` reject legal boilerplate ("En Cualquier Caso") and LLM placeholders ("Not specified in the text."). Applied in boe.py, extraction.py, phantombuster export |
| DB cleanup | DONE | Deleted corrupt lead 2534 (sole fake Tier A, boilerplate heirs). Cleaned 14 leads with junk heir names; 10 re-queued for extraction |
| Run summary persisted | DONE | `logs/run_summary_<ts>.json` per run + WARN for scrapers yielding 0 — silent scraper death is now visible |
| Export encoding | DONE | PhantomBuster CSV now utf-8-sig (was mojibake). Junk-name filter added to export |
| Extraction queue fix | DONE | Leads with no fetchable text are marked done instead of re-clogging the 200/run queue forever |

## Zero-Scraper Repair (2026-06-13)
The 6 all-time-zero scrapers were investigated and fixed. Verify any scraper in isolation with `python tools/smoke_test.py <name>` (see `docs/SMOKE_TESTING.md`) — no DB writes, no 23-min full run.
| Scraper | State | Root cause / note |
|---|---|---|
| boa | FIXED (12) | 90-day `since` filter dropped BOA's sparse archival data (API ignores date param); added 5yr floor |
| borme | FIXED (19) | BOE XML schema changed (`id`→`<identificador>`, `<emisor>` dropped); parser rewritten |
| cee | FIXED (100) | Wrong form id/AJAX mode; now `consultasForm` non-AJAX POST at aplicaciones.aragon.es/regcee, prov=4 |
| bop | FIXED config, UNVERIFIED | Wrong host `bop.dpz.es:80` → `https://boletin.dpz.es`; dev sandbox IP firewalled by DPZ — **verify on prod network** |
| rememori | PARSER FIXED, source dormant | Default alpha sort buried fresh deaths; now `direction:desc`. Site `.com`, newest entries 2022-24 → 0 on recent windows is correct |
| ite | GENUINELY DEAD | Zaragoza publishes no public ITE dataset (protected personal data); honest no-op with auto-activate detector |

**Inheritance layer (2026-06-13):** `ref_catastral` 0% is a **data ceiling, not a bug** — obituary leads are city-only (only ~86 of 3,396 have a street number). Catastro API works; added `lookup_rc_by_address`/`resolve_lead_addresses` (wired as run.py Step 7b2), fixed obras/subastas join (RC parcel OR address), fixed extraction.py to write `ref_catastral`/`address_norm`. Heir rate ~2% overall is a denominator effect (~37% on BOE-Notarial, the only source that names heirs).

**Known issues (open):** ~19% of obituary leads missing date_of_death (improved from 78%; urgency stuck on "monitoring"). A handful of leads have malformed causante (traspaso business titles in `causante`, tablon `causante='D'`). Emoji/cp1252 crash in PhantomBuster export is addressed by the UTF-8 stdout reconfigure now in `logging_config.py` (pending verification on next run).

**Delivery (2026-06-13):** Google Sheets + Gmail were **discontinued**. Delivery is now the self-hosted Flask dashboard (`nadia_ai.dashboard.app`, `/api/leads` + `/api/stats`) reading `nadia_ai.db` live. `deliver()` no longer calls Sheets/email — it just writes a local CSV snapshot, so the two recurring delivery errors are gone. `write_to_sheets`/`write_calibracion_tab` remain only for the INE calibration cron. Kanban board verified: leads bucket new_to_call / tax_followup (91-150d) / urgent (≥150d) / signed.

**Actionability (2026-06-13 assessment):** Identity 92% (causante) + location 99% (city) + date_of_death 81% + 100% outreach_allowed = a strong **prospecting list**. But **0% contact path** (`social_profile_url`) and **0% property linkage** (RC) mean it's not yet a turnkey call-list — each lead needs manual lookup. High-signal actionable core ≈ 296 leads (9%) from real inheritance edicts (BOE judicial/notarial + Tablón). **#1 lever to raise actionability: get PhantomBuster social enrichment producing profiles (currently exports CSV, 0 returned).**

## Geography (2026-06-12)
Obituary scrapers (esquelas, defunciones, rememori) and traspasos now run **Spain-wide by default** (50 provinces, Zaragoza/Huesca/Teruel scraped first). Per-client deployments narrow via `NADIA_PROVINCES=zaragoza` (comma list). `NADIA_PROVINCES=all` (default) = whole country. Every lead keeps its provincia so output can be filtered per client when reselling to agents outside Zaragoza.

## Next Steps
1. Push to GitHub and configure secrets
2. Manual trigger run to verify live data
3. Verify Calibracion tab populates on Monday INE cron
4. Monitor first week of production for dedup accuracy and false-positive rate
