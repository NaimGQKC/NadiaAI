"""Robust text decoding + Spanish-name reconstruction helpers.

Two production data-quality issues motivate this module:

1. Accent corruption. `requests` falls back to ISO-8859-1 when a server omits the
   charset header (RFC 2616), so UTF-8 accented bytes on Spanish sources that don't
   declare a charset get mis-decoded — silently corrupting every Ñ/á/é/í/ó/ú in
   heir and causante names. `robust_text` decodes from the raw bytes using charset
   detection instead of trusting that fallback.

2. Surname-less heirs. Esquelas list children by first name ("Sus hijos: Juan y
   María") because the surname is implied — the deceased's. `reconstruct_surname`
   appends the deceased's first apellido so a bare first name becomes an enrichable
   full name ("Juan" + deceased "García López" -> "Juan García").
"""

from __future__ import annotations

import unicodedata

# Common Spanish given names (shared bar with utils.names). A token that is a given
# name is not a surname; used to find where the surname portion of a name begins.
from nadia_ai.utils.names import _GIVEN_NAMES, _NAME_PARTICLES, _strip_accents


def robust_text(resp) -> str:
    """Decode a requests.Response to str, using real charset detection instead of the
    ISO-8859-1 fallback requests applies when the server omits a charset header."""
    declared = ""
    ctype = resp.headers.get("Content-Type", "").lower()
    if "charset=" in ctype:
        declared = ctype.split("charset=", 1)[1].split(";")[0].strip()
    # Trust an explicitly-declared charset; otherwise detect from the bytes.
    enc = declared or (resp.apparent_encoding or "").lower()
    if not enc or enc in ("iso-8859-1", "latin-1", "ascii"):
        # requests defaults to ISO-8859-1 with no header — prefer detection.
        enc = (resp.apparent_encoding or "utf-8")
    try:
        return resp.content.decode(enc, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return resp.content.decode("utf-8", errors="replace")


def _surname_tokens(deceased_name: str | None) -> list[str]:
    """Return the surname portion of a Spanish full name: the tokens after the leading
    run of given names/particles. 'Ángel García López' -> ['García', 'López']."""
    if not deceased_name:
        return []
    toks = [t for t in str(deceased_name).split() if t]
    i = 0
    # Skip leading given names + honorific/particle tokens (the "nombre" part).
    while i < len(toks):
        low = _strip_accents(toks[i]).lower().strip(".,")
        if low in _NAME_PARTICLES or (low in _GIVEN_NAMES and i < len(toks) - 1):
            i += 1
            continue
        break
    return toks[i:]


def reconstruct_surname(heir: str, deceased_name: str | None) -> str:
    """If `heir` is a bare given name (no surname), append the deceased's FIRST apellido
    so it becomes enrichable. Returns the heir unchanged when it already has a surname
    or no deceased surname is available."""
    if not heir or not heir.strip():
        return heir
    htoks = [t for t in heir.split() if t]
    core = [t for t in htoks
            if _strip_accents(t).lower() not in _NAME_PARTICLES and len(t) >= 3]
    # Already has a surname (a core token that is not a given name) → leave it.
    if any(_strip_accents(t).lower() not in _GIVEN_NAMES for t in core):
        return heir
    surnames = _surname_tokens(deceased_name)
    if not surnames:
        return heir
    return f"{heir.strip()} {surnames[0]}"
