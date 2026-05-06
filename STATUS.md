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
| Subastas BOE | subastas | Enrichment only | Variable | NO (Tier X) |
| Zaragoza Open Data Obras | obras | Enrichment only | Monthly batch | N/A |
| INE (calibration) | ine | Dashboard only | Weekly | N/A |

## Schema (Phase 2 additions)
- `tier` (A/B/C/X), `sources` (JSON array), `subsource`, `first_seen_at`
- `outreach_allowed` (bool), `outreach_notes` (string)
- `subasta_activa`, `obras_recientes` (enrichment cross-join)
- `nif`, `valor_tasacion`, `procedimiento` (BOE V.B / subastas)

## Files Added/Modified
```
NEW:  src/nadia_ai/merge.py           — Dedup, tier, outreach engine
NEW:  src/nadia_ai/ine.py             — INE calibration data fetcher
NEW:  src/nadia_ai/enrichment.py      — Subastas + Obras enrichment
NEW:  src/nadia_ai/scrapers/boe.py    — BOE TEJU + Section V scraper
NEW:  src/nadia_ai/scrapers/bop.py    — BOP Zaragoza scraper
NEW:  src/nadia_ai/scrapers/borme.py  — BORME scraper
NEW:  .github/workflows/weekly-ine.yml
NEW:  tests/unit/test_merge.py        — 25 dedup/tier/outreach tests
NEW:  docs/research/concursal-rejected.md
NEW:  docs/research/seccion-IIB-rejected.md
MOD:  src/nadia_ai/models.py          — LeadRow +2 enrichment cols (17 total)
MOD:  src/nadia_ai/db.py              — first_seen_at, enrichment schema ready
MOD:  src/nadia_ai/delivery.py        — Calibracion tab, conditional formatting, email subject
MOD:  src/nadia_ai/run.py             — BORME + enrichment pipeline steps
MOD:  src/nadia_ai/scrapers/boa.py    — 6 new extraction patterns
MOD:  README.md                        — Phase 2 architecture, new sources
MOD:  MANUAL_NADIA.md                 — Tier, outreach, fuentes explanation
MOD:  tests/ (multiple)               — Updated for 17-col schema, BORME mocks
```

## Next Steps
1. Push to GitHub and configure secrets
2. Manual trigger run to verify live data
3. Verify Calibracion tab populates on Monday INE cron
4. Monitor first week of production for dedup accuracy and false-positive rate
