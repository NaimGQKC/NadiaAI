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
