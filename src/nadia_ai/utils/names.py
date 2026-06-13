"""Person-name validation for heir/causante extraction.

Guards against two failure modes seen in production:
1. Regex fallback title-casing legal boilerplate ("En Cualquier Caso",
   "Según Lo Previsto Por El Art") into fake names.
2. LLM placeholder echoes stored as names ("Not specified in the text.",
   "Nombres completos no proporcionados en el texto").
"""

import re
import unicodedata

# Phrases that mark a string as boilerplate/placeholder, not a person name.
# Matched accent-insensitively on the lowercased string.
_BLACKLIST_SUBSTRINGS = [
    # LLM placeholder echoes
    "not specified", "no especificado", "no proporcionado", "no proporcionan",
    "no se proporciona", "no se menciona", "si se menciona", "no consta",
    "nombre completo", "nombres completos", "desconocido", "desconocida",
    "desconocidos", "heirs of", "herederos de", "heredero de", "unknown",
    "null", "n/a", "ejemplo", "fallecido", "causante", "el texto", "the text",
    # Legal/edict boilerplate
    "cualquier caso", "notificacion", "infructuoso", "ignorado", "domicilio",
    "previsto", "articulo", "art.", "expediente", "procedimiento", "juzgado",
    "notaria", "notario", "registro", "abintestato", "ab intestato",
    "intestado", "edicto", "boletin", "publicacion", "resolucion",
    "interesados", "se hace saber", "hace constar", "en su caso",
    "a quienes", "quienes se crean", "con derecho", "la herencia",
    "herencia yacente", "delegacion", "ministerio", "ayuntamiento",
    "secretaria", "tanatorio", "cementerio", "funeraria",
]

# A name should not *start* with a function word — that signals a captured
# clause, not a name ("En Cualquier Caso", "Cuyo Intento...", "De Los...").
_BAD_FIRST_WORDS = {
    "en", "de", "del", "la", "el", "los", "las", "y", "o", "u", "a", "al",
    "cuyo", "cuya", "cuyos", "cuyas", "segun", "para", "por", "con", "sin",
    "que", "se", "su", "sus", "este", "esta", "dicho", "dicha", "ante",
    "sobre", "tras", "como", "donde", "cuando", "si", "no", "ni", "lo",
}

# À-ÖØ-öø-ÿ covers Spanish, Catalan, and Galician accented letters (à è ò ï ç…)
_WORD_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-]+\.?$")


def _strip_accents(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def is_valid_person_name(name: str | None) -> bool:
    """Return True if the string plausibly is a Spanish person name."""
    if not name or not isinstance(name, str):
        return False
    name = name.strip().strip(".,;:")
    if len(name) < 5 or len(name) > 60:
        return False
    if re.search(r"[\d(){}\[\]:;\"<>@/\\|=+*_]", name):
        return False

    norm = _strip_accents(name).lower()
    if any(b in norm for b in _BLACKLIST_SUBSTRINGS):
        return False

    words = name.split()
    # Person names: 2-6 tokens (First + at least one surname)
    if len(words) < 2 or len(words) > 6:
        return False
    if _strip_accents(words[0]).lower() in _BAD_FIRST_WORDS:
        return False
    if not all(_WORD_RE.match(w) for w in words):
        return False
    # At least two substantive tokens (len >= 2, not connectors)
    substantive = [
        w for w in words if len(w) >= 2 and _strip_accents(w).lower() not in _BAD_FIRST_WORDS
    ]
    return len(substantive) >= 2


def clean_name_list(names: list | None) -> list[str]:
    """Filter a list of candidate names down to valid, deduped person names."""
    if not names:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for n in names:
        if not isinstance(n, str):
            continue
        n = n.strip().strip(".,;:")
        if not is_valid_person_name(n):
            continue
        key = _strip_accents(n).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out
