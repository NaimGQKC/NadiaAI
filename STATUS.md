# NadiaAI — Status

Last updated: 2026-04-28

## Current State: BUILD COMPLETE — NEEDS ENV VARS TO RUN LIVE

The full pipeline is built and tested. 73 unit tests pass, 75% coverage, lint clean.

## Architecture Pivot (IMPORTANT)

**Research finding:** Neither the Tablón de Edictos nor the BOA contain referencia catastral in their publications. The original thesis that these sources publish "with referencia catastral attached" was incorrect.

**Adapted approach:** NadiaAI v1 is a **lead discovery** system:
- Tablón scraper uses Zaragoza's excellent **JSON API** (no HTML scraping needed)
- Filters for `tipo.id=29` ("Acta de notoriedad/Declaracion de herederos")
- Extracts: deceased name, notary, dates, PDF link
- BOA scraper searches for Junta Distribuidora publications (~1-2 per year)
- Catastro client is ready for future use when addresses are available
- Sheet shows: Causante (deceased), Localidad, Source, Date, PDF link
- Nadia researches the property herself (she knows the neighborhoods)

This is still a strong competitive advantage — no other agent in Zaragoza is monitoring these sources.

## Done

| Phase | Status | Notes |
|-------|--------|-------|
| R1 Tablón research | Done | JSON API at zaragoza.es/sede/servicio/tablon-edicto.json |
| R2 BOA research | Done | CGI search + PDF only, ~1-2 publications/year |
| R3 Catastro research | Done | SOAP API, works for RC lookup |
| R4 Delivery research | Done | gspread + Gmail SMTP |
| TS Tech Stack | Done | Python 3.11+, requests, bs4, pydantic, gspread, sqlite |
| B1 Skeleton | Done | pyproject.toml, package layout, schema, Makefile |
| B2 Tablón scraper | Done | JSON API, name extraction from titles |
| B3 BOA scraper | Done | CGI search + HTML parser (PDF parsing can be added) |
| B4 Catastro client | Done | Lookup by RC, retry, cache |
| B5 Pipeline orchestrator | Done | python -m nadia_ai, TTL cleanup first |
| B6 Sheets + email | Done | gspread + Gmail SMTP, CSV fallback |
| B7 GitHub Actions | Done | .github/workflows/daily.yml, 07:00 UTC |
| QA | Done | 73 tests, 75% coverage, lint clean |
| Setup guide | Done | docs/SETUP_GOOGLE_SHEETS.md |
| README.md | Done | English, dev-facing |
| MANUAL_NADIA.md | Done | Spanish, agent-facing, includes Art. 14 GDPR letter template |

## Decisions Made

1. **Architecture pivot**: lead discovery (name-based) instead of property-level (RC-based)
2. **Tablón uses JSON API** — not HTML scraping (cleaner, faster, official open data)
3. **Sheet columns reordered**: Causante is now column C (the primary lead info)
4. **GitHub remote**: `NaimGQKC/NadiaAI` (added, not yet pushed)
5. **All Zaragoza city**, no neighborhood filter
6. **Google Sheet owned by dev**, shared with Nadia

## What's Left

### Must do before first live run:
1. Fill `.env` with real values (see `docs/SETUP_GOOGLE_SHEETS.md`)
2. Push to GitHub: `git add -A && git commit && git push -u origin main`
3. Add GitHub Actions secrets (listed in setup guide)

### Should do (next session):
1. Write `README.md` (English, dev-facing)
2. Write `MANUAL_NADIA.md` (Spanish, agent-facing, including letter template)
3. Add PDF parsing for Tablón PDFs (extract heir names, death details)
4. Add BOA PDF parsing with `pdfplumber`
5. Test live end-to-end with real Sheet and email
6. Consider: search deceased name against Catastro address index

## File Tree

```
NadiaAI/
├── .env.example
├── .gitignore
├── .github/workflows/daily.yml
├── Makefile
├── pyproject.toml
├── STATUS.md
├── docs/
│   ├── SETUP_GOOGLE_SHEETS.md
│   └── research/
│       ├── tablon.md
│       ├── boa.md
│       ├── catastro.md
│       ├── delivery.md
│       └── tech-stack-decision.md
├── src/nadia_ai/
│   ├── __init__.py
│   ├── __main__.py
│   ├── config.py
│   ├── db.py
│   ├── models.py
│   ├── logging_config.py
│   ├── run.py
│   ├── catastro.py
│   ├── delivery.py
│   └── scrapers/
│       ├── __init__.py
│       ├── tablon.py
│       └── boa.py
└── tests/
    ├── conftest.py
    └── unit/
        ├── test_models.py
        ├── test_db.py
        ├── test_parsers.py
        ├── test_catastro.py
        ├── test_enrich.py
        ├── test_delivery.py
        ├── test_pipeline.py
        ├── test_logging.py
        └── test_boa_parser.py
```
