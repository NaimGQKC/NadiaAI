# NadiaAI

Predictive lead generation for a real-estate agent in Zaragoza, Spain. Monitors inheritance declarations from public sources to identify properties that will hit the market in 3-18 months.

## How it works

```
Tablón de Edictos (JSON API)  ──┐
                                ├──> SQLite ──> Google Sheets + Email
BOA Junta Distribuidora (CGI)  ──┘
```

1. **Tablón scraper** polls Zaragoza's open-data API for `declaración de herederos abintestato` records
2. **BOA scraper** checks for Junta Distribuidora de Herencias publications (~1-2/year)
3. **Catastro client** enriches with property data when a referencia catastral is available
4. **Delivery** appends new leads to a Google Sheet and sends a daily email summary

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
├── catastro.py         # Catastro public API client
├── delivery.py         # Google Sheets + email delivery
├── logging_config.py   # Structured JSON logging (cron) / human-readable (local)
└── scrapers/
    ├── tablon.py       # Zaragoza Tablón de Edictos (JSON API)
    └── boa.py          # BOA Junta Distribuidora (CGI search + HTML)
```

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
3. Add the call to `run.py` in the pipeline sequence
4. Add unit tests with fixtures in `tests/fixtures/your_source/`
5. The delivery layer handles the rest automatically

## Legal posture

NadiaAI works exclusively with **publicly published data** from official government gazettes (Tablón de Edictos, BOA). It operates at the property level where possible and limits person-level data to a 24-month TTL with automatic deletion. Outreach is postal mail only, including the Art. 14 GDPR notice. No bulk lookups against protected registries (Padrón, Censo Electoral, Registro Civil). See `MANUAL_NADIA.md` for the full legal framework in Spanish.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `ModuleNotFoundError: nadia_ai` | Run `pip install -e .` from the repo root |
| Google Sheets 403 | Share the sheet with the service account email |
| Gmail "less secure apps" error | Use an app password, not your account password |
| Tablón returns 0 records | Normal — records expire after ~30 days; check `tipo.id=29` still works |
| Pipeline runs but no email | Check `MAMA_EMAIL`, `SMTP_USER`, `SMTP_PASSWORD` in `.env` |
