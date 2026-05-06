# NadiaAI v2

Predictive lead generation for a real-estate agent in Zaragoza, Spain. Monitors inheritance declarations from public sources to identify properties that will hit the market in 3-18 months.

## How it works

```
                       ┌─ Tablón de Edictos (JSON API)
                       ├─ BOA Junta Distribuidora (CGI)
  Discovery sources ───├─ BOE TEJU (Tribunal notices, XML)
                       ├─ BOE Sección V (notarial edicts, XML)
                       ├─ BORME I + II (company dissolutions/deaths, XML)
                       └─ BOP Zaragoza (provincial gazette, HTML)
                                │
                                ▼
                        merge.py (dedup + tier)
                                │
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
              Enrichment    INE calib.   SQLite
           (Subastas, Obras)  (pop. data)   │
                    │           │           │
                    └───────────┼───────────┘
                                ▼
                     Google Sheets + Email
```

1. **Tablón scraper** polls Zaragoza's open-data API for `declaracion de herederos abintestato` records
2. **BOA scraper** checks for Junta Distribuidora de Herencias publications (~1-2/year)
3. **BOE scraper** fetches TEJU (tribunal edicts) and Seccion V (notarial edicts) from the BOE XML feed
4. **BORME scraper** detects company dissolutions and administrator deaths from BORME I and II
5. **BOP scraper** parses the Boletin Oficial de la Provincia de Zaragoza for local inheritance edicts
6. **Merge** deduplicates leads across sources, assigns tiers (A/B/C/X), and flags outreach eligibility
7. **Enrichment** adds context from Subastas (active auctions) and Obras (recent construction)
8. **INE calibration** adjusts lead scoring using population and demographic data
9. **Catastro client** enriches with property data when a referencia catastral is available
10. **Delivery** appends new leads to a Google Sheet and sends a daily email summary

Runs daily at 08:00 Europe/Madrid via GitHub Actions.

## Quickstart

```bash
pip install -e ".[dev]"
cp .env.example .env       # fill in your values — see docs/SETUP_GOOGLE_SHEETS.md
python -m nadia_ai
```

## Architecture

```
src/nadia_ai/
├── run.py              # Pipeline orchestrator (entry point)
├── config.py           # Env var loading
├── db.py               # SQLite schema + operations
├── models.py           # Pydantic models (EdictRecord, ParcelInfo, LeadRow)
├── merge.py            # Cross-source dedup, tier assignment, outreach flagging
├── catastro.py         # Catastro public API client
├── ine.py              # INE population/demographic calibration
├── enrichment.py       # Subastas + Obras context enrichment
├── delivery.py         # Google Sheets + email delivery
├── logging_config.py   # Structured JSON logging (cron) / human-readable (local)
└── scrapers/
    ├── tablon.py       # Zaragoza Tablón de Edictos (JSON API)
    ├── boa.py          # BOA Junta Distribuidora (CGI search + HTML)
    ├── boe.py          # BOE TEJU + Sección V (XML feed)
    ├── bop.py          # BOP Zaragoza (provincial gazette, HTML)
    └── borme.py        # BORME I + II (company dissolutions/deaths, XML)
```

## Lead schema

Each lead row includes the following fields:

| Field | Description |
|-------|-------------|
| `fecha_deteccion` | When NadiaAI detected the edict |
| `fuente` | Primary source (Tablon, BOA, BOE, BORME, BOP) |
| `sources` | All sources that contributed to this lead (comma-separated) |
| `subsource` | Specific subsource (e.g., TEJU, Sec.V, BORME-I, BORME-II) |
| `causante` | Name of the deceased |
| `localidad` | City |
| `ref_catastral` | Catastro reference, if known |
| `direccion` | Property address, if known |
| `m2` | Area in square meters, if known |
| `tipo_inmueble` | Property type (residential, commercial, etc.) |
| `tier` | Lead quality tier: A (best), B, C, or X (context only) |
| `outreach_allowed` | Whether direct outreach is permitted (true/false) |
| `outreach_notes` | Explanation when outreach is restricted |
| `subasta_activa` | Whether the property has an active auction |
| `obras_recientes` | Whether recent construction permits exist |
| `nif` | NIF of the causante or company, if available |
| `valor_tasacion` | Appraised value from auction data, if available |
| `procedimiento` | Court/notarial proceeding reference |
| `estado` | User-managed status (Nuevo, En seguimiento, etc.) |
| `notas` | Free-text notes |
| `link_edicto` | Link to the original edict |

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Yes | Path to GCS service account JSON |
| `LEADS_SHEET_ID` | Yes | Google Sheet ID |
| `SMTP_USER` | Yes | Gmail address for sending |
| `SMTP_PASSWORD` | Yes | Gmail app password |
| `MAMA_EMAIL` | Yes | Nadia's email address |
| `DEV_ALERT_EMAIL` | No | Dev email for failure alerts |
| `NADIA_DB_PATH` | No | SQLite path (default: `nadia_ai.db`) |
| `PERSON_TTL_DAYS` | No | Person data TTL (default: 730 = 24 months) |
| `CATASTRO_CACHE_DAYS` | No | Catastro cache duration (default: 30) |

See `docs/SETUP_GOOGLE_SHEETS.md` for full setup instructions.

## Testing

```bash
make test              # unit tests only
make test-integration  # unit + integration
make test-all          # everything including e2e
make lint              # ruff check + format check
```

## Adding a new source

1. Create `src/nadia_ai/scrapers/your_source.py`
2. Implement a `scrape_*()` function returning `list[EdictRecord]`
3. Set the `source` and `subsource` fields on each record so merge can deduplicate correctly
4. Add the call to `run.py` in the pipeline sequence
5. Register any new tier/outreach rules in `merge.py`
6. Add unit tests with fixtures in `tests/fixtures/your_source/`
7. The delivery layer handles the rest automatically

## Legal posture

NadiaAI works exclusively with **publicly published data** from official government gazettes:

- **Tablon de Edictos** — Zaragoza municipal edict board
- **BOA** — Boletin Oficial de Aragon (regional gazette)
- **BOE** — Boletin Oficial del Estado (national gazette: TEJU tribunal edicts, Seccion V notarial edicts)
- **BORME** — Boletin Oficial del Registro Mercantil (company registry gazette)
- **BOP Zaragoza** — Boletin Oficial de la Provincia (provincial gazette)

All sources are official gazettes published by government bodies for public consultation. NadiaAI operates at the property level where possible and limits person-level data to a 24-month TTL with automatic deletion. Outreach is postal mail only, including the Art. 14 GDPR notice. No bulk lookups against protected registries (Padron, Censo Electoral, Registro Civil). Tier X leads (distress-related: auctions, insolvency) are flagged as context-only with outreach disabled. See `MANUAL_NADIA.md` for the full legal framework in Spanish.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `ModuleNotFoundError: nadia_ai` | Run `pip install -e .` from the repo root |
| Google Sheets 403 | Share the sheet with the service account email |
| Gmail "less secure apps" error | Use an app password, not your account password |
| Tablon returns 0 records | Normal — records expire after ~30 days; check `tipo.id=29` still works |
| Pipeline runs but no email | Check `MAMA_EMAIL`, `SMTP_USER`, `SMTP_PASSWORD` in `.env` |
| BOE XML parse error | Check if BOE changed their XML schema; see `scrapers/boe.py` |
| BORME returns empty | BORME only publishes on business days; weekends/holidays are normal |
