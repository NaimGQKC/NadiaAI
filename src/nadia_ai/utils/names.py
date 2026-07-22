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
    # Institutions named as heir (the State/CCAA inherits when there are no
    # relatives) — never a person.
    "comunidad autonoma", "autonoma de", "generalitat", "generalidad",
    "diputacion", "gobierno de", "junta de", "estado", "tesoreria", "hacienda",
    "agencia tributaria", "fundacion", "asociacion", "sociedad", "cooperativa",
    "consorcio", "patronato", "universidad", "instituto", "parroquia", "iglesia",
    # Edict phrasing that reads like a title-cased name
    "pudiendo", "acompanad", "cuantos se crean", "ignorados",
]

# Kinship/role words. A real person name never contains these as a standalone
# token — when they appear it's a relationship descriptor captured instead of a
# name (e.g. notarial titles: "Hijo de Don Federico", "Viuda de ..."). Note: the
# honorifics "Don"/"Doña" are deliberately NOT here — they legitimately prefix
# real heir names ("Don Iván Raúl Cabrera Cerpa").
_RELATIONSHIP_WORDS = {
    "hijo", "hija", "hijos", "hijas", "viudo", "viuda", "viudos", "viudas",
    "esposo", "esposa", "esposos", "esposas", "conyuge", "consorte",
    "nieto", "nieta", "nietos", "nietas", "sobrino", "sobrina", "sobrinos",
    "sobrinas", "hermano", "hermana", "hermanos", "hermanas", "padre", "madre",
    "heredero", "heredera", "herederos", "herederas", "legatario", "legataria",
    "primo", "prima", "primos", "primas", "abuelo", "abuela", "tio", "tia",
}

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

# Common Spanish given names — a 2-token name that is ALL given names is a compound
# first name with no surname captured ("Juan José", "María Pilar"); reject it.
_GIVEN_NAMES = {
    "juan", "jose", "maria", "pilar", "carmen", "ana", "luis", "pedro", "angel",
    "jesus", "antonio", "francisco", "manuel", "javier", "miguel", "jorge", "carlos",
    "david", "daniel", "pablo", "sergio", "alberto", "alejandro", "fernando", "ramon",
    "rafael", "vicente", "ignacio", "andres", "alfonso", "enrique", "emilio", "tomas",
    "ruben", "oscar", "mario", "adrian", "marcos", "ivan", "victor", "diego", "raul",
    "isabel", "dolores", "josefa", "teresa", "rosa", "francisca", "antonia", "cristina",
    "laura", "marta", "elena", "sara", "paula", "lucia", "sofia", "nuria", "raquel",
    "beatriz", "rocio", "montserrat", "silvia", "patricia", "susana", "monica",
    "angeles", "mercedes", "concepcion", "manuela", "encarnacion", "julia", "eva",
    "irene", "alba", "clara", "juana", "amparo", "consuelo", "gloria", "esther",
}

# Lowercase-legit particles/honorifics — excluded from the accent-corruption check
# (a real name token is capitalized; a stripped accent leaves a lowercase fragment).
_NAME_PARTICLES = {
    "de", "del", "la", "el", "los", "las", "y", "e", "o", "u", "a", "al", "san",
    "santa", "da", "do", "dos", "das", "di", "van", "von", "der", "den", "le",
    "don", "dona", "dna", "sr", "sra", "srta", "vda", "viuda",
}

# Leading courtesy titles that prefix a name in prose/headlines but are not part
# of it ("Doña Carmen Alejandre" → "Carmen Alejandre"). Sources whose name regex
# already excludes the honorific (BOE/BOA) don't need this; raw-title sources
# (Heraldo esquelas) do. Order matters — longer/accented forms first.
# Trailing [.ªº]* absorbs the abbreviated feminine forms "Dª", "D.ª", "D.º".
_HONORIFIC_RE = re.compile(
    r"^(?:do[ñn]a|don|d[ñn]a|sr(?:a|ta)?|se[ñn]or(?:a|ita)?|d)[.ªº]*\s+",
    re.IGNORECASE,
)


def strip_honorific(name: str | None) -> str:
    """Strip a leading Spanish courtesy title (Don/Doña/Dña/D./Sr./Sra.) from a
    name. Idempotent; returns the input unchanged when no honorific is present."""
    if not name:
        return name or ""
    cleaned = _HONORIFIC_RE.sub("", name.strip(), count=1).strip()
    # Only accept the strip if something substantive remains; otherwise the
    # "name" was just a title and we keep the original for the validator to reject.
    return cleaned or name.strip()


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
    # A kinship/role token anywhere means we captured a relationship phrase
    # ("Hijo De Don Federico"), not a name.
    if any(_strip_accents(w).lower() in _RELATIONSHIP_WORDS for w in words):
        return False
    if not all(_WORD_RE.match(w) for w in words):
        return False
    # At least two substantive tokens (len >= 2, not connectors)
    substantive = [
        w for w in words if len(w) >= 2 and _strip_accents(w).lower() not in _BAD_FIRST_WORDS
    ]
    if len(substantive) < 2:
        return False
    # Accent-corruption guard: a real name token is capitalized; a stripped accent
    # leaves a lowercase-initial fragment ("ngel" <- "Ángel"). Reject those (but not
    # legit lowercase particles/honorifics like "de la" / "don").
    for w in substantive:
        if _strip_accents(w).lower() in _NAME_PARTICLES:
            continue
        first_alpha = next((c for c in w if c.isalpha()), "")
        if first_alpha and first_alpha.islower():
            return False
    # Compound-first-name-only guard: if every substantive token is a common given
    # name, no surname was captured ("Juan José", "María Pilar") — not enrichable.
    core = [_strip_accents(w).lower() for w in substantive
            if _strip_accents(w).lower() not in _NAME_PARTICLES and len(w) >= 3]
    if len(core) >= 2 and all(t in _GIVEN_NAMES for t in core):
        return False
    return True


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
