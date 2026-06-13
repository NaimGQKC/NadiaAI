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

# Ollama (local LLM — free; only reachable when running on a machine with Ollama,
# NOT in the GitHub Actions cron). Used as the fallback when no cloud key is set.
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral")

# Anthropic (cloud LLM — best Spanish legal extraction, works anywhere incl. CI).
# When ANTHROPIC_API_KEY is set, heir/address extraction uses Claude; otherwise it
# falls back to Ollama, then to regex. ANTHROPIC_MODEL is overridable — claude-haiku-4-5
# is ~5x cheaper than the opus default and ample for this short-text extraction.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("NADIA_ANTHROPIC_MODEL", "claude-opus-4-8")

# Phantombuster (free tier social graphing)
PHANTOMBUSTER_API_KEY = os.getenv("PHANTOMBUSTER_API_KEY", "")

# ── Contact-discovery enrichment (search-native LLM) ───────────────────────
# Turns a heir/causante name + city into a contact path (phone/email/profile)
# by querying a *search-native* model. Claude has no search index, so this uses
# Perplexity Sonar (primary) — purpose-built for web search and returns citations
# we keep for identity verification + GDPR defensibility. Gemini-with-grounding is
# an optional alternate provider. Provider-agnostic, mirrors utils/extraction.py.
#
# Perplexity's API surface has shifted between releases (/chat/completions vs
# /v1/* endpoints), so the URL is overridable to avoid a code change if it moves.
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")
PERPLEXITY_API_URL = os.getenv(
    "PERPLEXITY_API_URL", "https://api.perplexity.ai/chat/completions"
)
PERPLEXITY_MODEL = os.getenv("PERPLEXITY_MODEL", "sonar")

# Optional alternate provider: Gemini 2.x Flash with Google Search grounding.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Cost control: cap how many leads we spend a paid search on per run.
CONTACT_ENRICH_MAX_PER_RUN = int(os.getenv("CONTACT_ENRICH_MAX_PER_RUN", "50"))

# Dashboard
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "5000"))
