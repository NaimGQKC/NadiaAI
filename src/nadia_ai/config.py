"""Configuration loaded from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Database
DB_PATH = Path(os.getenv("NADIA_DB_PATH", "nadia_ai.db"))

# Google Sheets
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
LEADS_SHEET_ID = os.getenv("LEADS_SHEET_ID", "")

# Email
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
MAMA_EMAIL = os.getenv("MAMA_EMAIL", "")
DEV_ALERT_EMAIL = os.getenv("DEV_ALERT_EMAIL", "")

# Pipeline
PERSON_TTL_DAYS = int(os.getenv("PERSON_TTL_DAYS", "730"))  # 24 months
CATASTRO_CACHE_DAYS = int(os.getenv("CATASTRO_CACHE_DAYS", "30"))

# Geographic focus for obituary scrapers (defunciones, rememori, esquelas).
# Zaragoza/Aragón first (home market), then the rest of Spain — the product is
# sold per-province to other agents, so every lead is tagged with its provincia.
# Override with NADIA_PROVINCES=zaragoza,madrid (comma list) to narrow a
# deployment to one client's territory, or NADIA_PROVINCES=all for everything.
SPAIN_PROVINCES = [
    # Home market first — scraped before the rest so a slow run still covers Aragón
    "zaragoza", "huesca", "teruel",
    "madrid", "barcelona", "valencia", "sevilla", "alicante", "malaga",
    "murcia", "cadiz", "vizcaya", "coruna", "asturias", "pontevedra",
    "granada", "tarragona", "cordoba", "gerona", "almeria", "guipuzcoa",
    "toledo", "badajoz", "navarra", "jaen", "castellon", "cantabria",
    "valladolid", "ciudad-real", "huelva", "leon", "lerida", "caceres",
    "albacete", "burgos", "salamanca", "lugo", "orense", "la-rioja",
    "alava", "guadalajara", "segovia", "zamora", "avila", "cuenca",
    "palencia", "soria", "baleares", "las-palmas", "tenerife",
]

_provinces_env = os.getenv("NADIA_PROVINCES", "all").strip().lower()
SPAIN_WIDE = _provinces_env in ("", "all", "spain")
if SPAIN_WIDE:
    TARGET_PROVINCES = SPAIN_PROVINCES
else:
    TARGET_PROVINCES = [p.strip().lower() for p in _provinces_env.split(",") if p.strip()]

# Ollama (local LLM — free; used ONLY by the standalone qa_judge dev tool now,
# not the pipeline, which runs on the configured extraction model + Perplexity).
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral")

# Phantombuster (free tier social graphing)
PHANTOMBUSTER_API_KEY = os.getenv("PHANTOMBUSTER_API_KEY", "")

# ── Two LLMs, one job each ─────────────────────────────────────────────────
# Extraction (read edict/obituary text → JSON): a cheap OpenAI-compatible model.
# Provider-neutral — point the three EXTRACTION_* vars at any of the cheap
# Chinese models without touching code. Defaults to DeepSeek:
#   DeepSeek: https://api.deepseek.com/chat/completions                          model deepseek-chat
#   Qwen:     https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions  model qwen-plus
#   MiniMax:  https://api.minimax.io/v1/chat/completions (verify current path)   model abab/MiniMax-*
# (JSON mode via response_format is sent; DeepSeek/Qwen support it. If a provider
#  rejects it, extraction simply falls back to regex — no crash.)
# Contact search (name → web → contact): Perplexity Sonar — search-native, cited.
# Both are OpenAI-compatible and work in CI.
EXTRACTION_API_KEY = os.getenv("EXTRACTION_API_KEY", "")
EXTRACTION_API_URL = os.getenv("EXTRACTION_API_URL", "https://api.deepseek.com/chat/completions")
EXTRACTION_MODEL = os.getenv("EXTRACTION_MODEL", "deepseek-chat")

PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")
PERPLEXITY_API_URL = os.getenv("PERPLEXITY_API_URL", "https://api.perplexity.ai/chat/completions")
PERPLEXITY_MODEL = os.getenv("PERPLEXITY_MODEL", "sonar")

# B2B registry enrichment (eInforma / Axesor) — Spain's "skip-trace equivalent"
# for company leads (borme, traspasos). Paid; the source is a stub until a key +
# implementation are added, at which point it slots into the waterfall as tier 1.
EINFORMA_API_KEY = os.getenv("EINFORMA_API_KEY", "")

# ── Heir contact-resolution providers (see contact_resolve.py / docs/contact_methods.md)
# Both OFF by default (empty key) — same stub pattern as EINFORMA_API_KEY: the
# resolver self-skips until a key + an implementation exist, so the contact
# waterfall falls through to the deterministic postal/notaría paths. No secrets.
#
# Registro de la Propiedad "Servicio de Índices": resolve an heir's NAME (+NIF) to
# the municipalities where they hold property, then a nota simple (~€9/finca) for
# the registered domicile. Paid + gated on a register-vetted interés legítimo.
REGISTRO_INDICES_API_KEY = os.getenv("REGISTRO_INDICES_API_KEY", "")

# Paid skip-trace / "localización de personas" broker (debt-collection / detective
# grade). The only realistic name→phone path in Spain; modest cold-name coverage,
# needs a DPA. One slot, env-gated, no real key.
CONTACT_SKIPTRACE_API_KEY = os.getenv("CONTACT_SKIPTRACE_API_KEY", "")

# Cost control: cap how many leads we spend a paid contact-search on per run.
# DEFAULT 0 (off): measured 2026-06-14, web contact-search for citizen heirs yields
# ~0% — Spain has no public phone directory (páginas blancas were restricted) and
# common names can't be disambiguated from homonyms, so Perplexity correctly returns
# identity_match=false. Spending here is pure waste. The real contact path is the
# handling office (notaría/juzgado), now filled deterministically from the edict text
# by utils.edict_parse — no LLM needed. Re-enable (set >0) only for B2B/registry
# enrichment via a real skip-trace provider (EINFORMA_API_KEY), or to run a manual
# pass on a hand-picked high-value cohort.
CONTACT_ENRICH_MAX_PER_RUN = int(os.getenv("CONTACT_ENRICH_MAX_PER_RUN", "0"))

# Resolve the handling NOTARY's public phone (search-native, uses Perplexity).
# OFF by default: the client wants the heir's contact, not the notaría's, and the
# search was 401-spamming + burning ~7 min of budget. Set RESOLVE_NOTARY_PHONE=1
# to re-enable (it is still the only legal warm channel for the notarial cohort).
RESOLVE_NOTARY_PHONE = os.getenv("RESOLVE_NOTARY_PHONE", "0") == "1"

# Resolve the HEIR's phone via RocketReach (commercial B2B contact-data API). OFF
# by default; needs ROCKETREACH_API_KEY + RESOLVE_HEIR_PHONE=1. Honest yield is low
# for private heirs (RocketReach is professional/LinkedIn-oriented) and name+city
# alone risks wrong-person matches — treated as low-confidence. Storing EU personal
# phone data requires a lawful basis under GDPR before any outreach.
ROCKETREACH_API_KEY = os.getenv("ROCKETREACH_API_KEY", "")
RESOLVE_HEIR_PHONE = os.getenv("RESOLVE_HEIR_PHONE", "0") == "1"
ROCKETREACH_API_URL = os.getenv(
    "ROCKETREACH_API_URL", "https://api.rocketreach.co/api/v2/person/lookup"
)

# ── Outreach generation (lead → ready-to-send Spanish call script / WhatsApp /
# letter) ──────────────────────────────────────────────────────────────────────
# Reuses the provider-neutral OpenAI-compatible endpoint by default. Override
# OUTREACH_MODEL to point at a more capable model for customer-facing copy — the
# quality bar is higher here than for extraction. The LLM only ever polishes a
# *template with {placeholders}* (no personal data leaves the machine); real names
# are filled locally, so this stays GDPR-clean. Falls back to hand-written
# deterministic templates when no key is set — outreach never depends on the LLM.
OUTREACH_API_KEY = os.getenv("OUTREACH_API_KEY", EXTRACTION_API_KEY)
OUTREACH_API_URL = os.getenv("OUTREACH_API_URL", EXTRACTION_API_URL)
OUTREACH_MODEL = os.getenv("OUTREACH_MODEL", EXTRACTION_MODEL)

# Agent identity injected into generated outreach (personalize per deployment).
AGENT_NAME = os.getenv("NADIA_AGENT_NAME", "[Tu nombre]")
AGENT_AGENCY = os.getenv("NADIA_AGENT_AGENCY", "RE/MAX")
AGENT_PHONE = os.getenv("NADIA_AGENT_PHONE", "[tu teléfono]")

# FSBO ("venta por particular") feed — pisos.com localidad slugs to sweep in the
# daily run (home market by default). Comma list, e.g. "zaragoza_capital,huesca".
FSBO_LOCALIDADES = [
    s.strip() for s in os.getenv("FSBO_LOCALIDADES", "zaragoza_capital").split(",") if s.strip()
]

# Dashboard
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "5000"))
