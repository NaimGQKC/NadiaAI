# Tech Stack Decision — NadiaAI

Author: TS (Tech Strategy Agent)
Date: 2026-04-28

## Constraints

- Single developer, weekend build
- Free tier only (GitHub Actions, Gmail, Google Sheets API)
- Single-user system — no auth, no multi-tenancy
- Daily cron with ~3 sources, 1-50 rows/day — no high-throughput requirements
- Must handle Spanish locale (ñ, accents, UTF-8 everywhere)

## Decisions

### Python 3.11
**Chosen.** Stable, widely supported on GitHub Actions, has all needed stdlib modules.
- *Rejected: 3.12/3.13* — no features we need; risk of dependency incompatibility on Actions runners.

### HTTP Client: `requests` 2.31+
**Chosen.** Synchronous, simple, battle-tested. For a daily cron hitting 3 endpoints sequentially, async adds complexity with zero benefit.
- *Rejected: `httpx`* — async capability unnecessary; adds a dependency for no gain at this scale.

### HTML Parsing: `beautifulsoup4` + `lxml`
**Chosen.** `lxml` as the parser backend gives speed and XPath support. `beautifulsoup4` provides a forgiving API for messy government HTML.
- *Rejected: `selectolax`* — faster but less forgiving on malformed HTML, smaller community, worse docs.

### Data Validation: `pydantic` v2
**Chosen.** Type-safe models, good error messages, serialization for free. v2 is significantly faster than v1.
- *Rejected: `dataclasses` + manual validation* — more boilerplate, no built-in serialization.
- *Rejected: `attrs`* — adequate but less ecosystem support than pydantic for this use case.

### Storage: SQLite via stdlib `sqlite3`
**Chosen.** Zero-config, single-file database, perfect for single-user system with <10K rows. WAL mode for safe concurrent reads during cron.
- *Rejected: `sqlalchemy`* — ORM overhead unjustified at this scale. We have 3 tables.
- *Rejected: PostgreSQL* — needs a server; absurd for a single-user daily cron.

### Sheets Client: `gspread` 6+
**Chosen.** Most popular Python Google Sheets library. Service account auth is well-documented. Supports batch operations, formatting, and all our needs.
- *Rejected: `google-api-python-client`* — lower-level, more boilerplate for the same operations.

### Email: `smtplib` + `email.mime` (stdlib) via Gmail SMTP
**Chosen.** Zero dependencies. Gmail app password + SMTP is reliable for 1 email/day. Free forever.
- *Rejected: Resend* — excellent API but unnecessary dependency and requires DNS setup for custom domain.
- *Rejected: Mailgun* — free tier requires credit card; overkill for 1 email/day.

### Scheduling: GitHub Actions cron
**Chosen.** Free for public repos (2,000 min/month), 500 min/month for private. A pipeline run takes <2 minutes. Even on private repos, 500 min / 2 min = 250 runs/month = ~8/day. Daily cron is well within limits.
- *Rejected: Railway/Render cron* — adds a platform dependency; Actions is already in the workflow.
- *Rejected: Local crontab* — requires a running machine; unreliable for a non-technical user.

### Testing: `pytest` + `responses` + `coverage.py`
**Chosen.** `pytest` is the standard. `responses` mocks HTTP calls cleanly without VCR cassette management overhead. `coverage.py` via `pytest-cov` plugin.
- *Rejected: `pytest-vcr`* — cassette files are brittle with government sites that change headers/cookies frequently.
- *Rejected: `unittest`* — more verbose, fewer fixtures, no plugins.

### Linting/Formatting: `ruff`
**Chosen.** Single tool replaces flake8, isort, black, and pyupgrade. Fast, zero-config for our needs.
- *Rejected: black + flake8 + isort* — three tools doing what one does better.

### Dependency Management: `pip` + `pyproject.toml`
**Chosen.** Standard, works everywhere, no extra tooling. `pyproject.toml` with `[project.optional-dependencies]` for dev deps.
- *Rejected: `uv`* — excellent but adds an install step on GitHub Actions that may not be pre-installed. Not worth the friction for a small project.
- *Rejected: `poetry`* — heavy, opinionated, `pyproject.toml` is sufficient.

### Logging: stdlib `logging` with structured JSON for cron
**Chosen.** JSON formatter for GitHub Actions (parseable), human-readable for local dev. Custom formatter, zero deps.
- *Rejected: `structlog`* — great library, unnecessary dependency for this.

### Secrets: `.env` via `python-dotenv`
**Chosen.** Standard pattern. `.env` in `.gitignore`, `.env.example` committed with placeholders.
- *Rejected: Vault/AWS Secrets Manager* — absurd for a single-user local system.

## Dependency Summary

```
# Core
requests>=2.31
beautifulsoup4>=4.12
lxml>=5.0
pydantic>=2.0
gspread>=6.0
google-auth>=2.0
python-dotenv>=1.0

# Dev
pytest>=8.0
pytest-cov>=5.0
responses>=0.25
ruff>=0.4
coverage>=7.0
```

Total: 7 runtime deps, 5 dev deps. Lean.
