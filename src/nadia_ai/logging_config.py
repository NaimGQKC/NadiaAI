"""Logging configuration — no PII in logs."""

import json
import logging
import sys
from datetime import UTC, datetime


class StructuredFormatter(logging.Formatter):
    """JSON formatter for cron runs, human-readable for local dev."""

    def __init__(self, structured: bool = False):
        super().__init__()
        self.structured = structured

    def format(self, record: logging.LogRecord) -> str:
        if self.structured:
            return json.dumps(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "level": record.levelname,
                    "logger": record.name,
                    "msg": record.getMessage(),
                }
            )
        return f"[{record.levelname:7s}] {record.name}: {record.getMessage()}"


def setup_logging(structured: bool = False, level: int = logging.INFO) -> None:
    """Configure logging for the pipeline. No PII — use ref catastral, not names."""
    # Windows defaults stdout/stderr to cp1252, which raises UnicodeEncodeError
    # on accented Spanish names and the occasional emoji in scraped listing
    # titles (e.g. a "Pizzería … 🍕" traspaso). Force UTF-8 so neither logging
    # nor bare print() can abort a pipeline step on non-Latin-1 characters.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter(structured=structured))

    root = logging.getLogger("nadia_ai")
    root.setLevel(level)
    # Avoid duplicate handlers on repeated calls
    if not root.handlers:
        root.addHandler(handler)

    # Suppress noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("gspread").setLevel(logging.WARNING)
