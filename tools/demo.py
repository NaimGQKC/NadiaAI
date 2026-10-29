"""Loom-friendly walkthrough of ONE lead: document -> target fields -> JSON -> email.

Runs the real parsing, validation, tiering and identity-verification code on a
SYNTHETIC edict (invented people, no personal data). Only the two external API
responses (the LLM's JSON and the enrichment provider's reply) are recorded
samples, and the output says so. No network, no credits.

    python tools/demo.py              # paced for recording
    DEMO_PAUSE=0 python tools/demo.py # instant
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nadia_ai.utils.edict_parse import parse_notaria_edict  # noqa: E402
from nadia_ai.utils.names import clean_name_list  # noqa: E402
import test_heir_phones as tp  # noqa: E402

PAUSE = float(os.getenv("DEMO_PAUSE", "1.6"))
C = {"hl": "\033[1;30;43m", "b": "\033[1m", "g": "\033[32m", "r": "\033[31m",
     "y": "\033[33m", "d": "\033[2m", "c": "\033[36m", "x": "\033[0m"}
if not sys.stdout.isatty() and os.getenv("DEMO_COLOR") != "1":
    C = {k: "" for k in C}

# Fields we target, in the order they appear in the document: (label, text in document)
EDICT = (
    "NOTARÍA DE LUCÍA ARNAL VIDAL DE ZARAGOZA. Anuncio de notificación de 14 de "
    "julio de 2026 en procedimiento Declaración de herederos ab intestato de don "
    "Ramón Ibarra Salas, vecino de Zaragoza. Se comunica a las personas ignoradas "
    "que por su hija doña Elena Ibarra Pons se está tramitando Acta de Notoriedad "
    "con el número 842 de mi protocolo de 2026, siendo su último domicilio en "
    "Calle Ejemplo, 12, 3º B, de Zaragoza. En el plazo de veinte días podrán "
    "comparecer en mi despacho profesional sito en Paseo Muestra, 5, de Zaragoza, "
    "a fin de oponerse."
)
TARGETS = [("CAUSANTE (deceased)", "Ramón Ibarra Salas"),
           ("HEIR", "Elena Ibarra Pons"),
           ("ADDRESS (last home)", "Calle Ejemplo, 12, 3º B"),
           ("NOTARY", "LUCÍA ARNAL VIDAL")]

# Recorded model output for this document (shape = what the extraction step returns).
LLM_JSON = {
    "deceased_name": "Ramón Ibarra Salas",
    "date_of_death": "2026-06-30",
    "list_of_heirs": ["Elena Ibarra Pons", "Comunidad Autónoma de Aragón", "Marcos Ibarra Pons"],
    "property_address": "Calle Ejemplo, 12, 3º B, Zaragoza",
    "referencia_catastral": None,
}

# Recorded enrichment-provider replies for two candidates (invented).
CANDIDATES = [
    {"label": "candidate 1", "matched_full": "Elena Ibarra Pons", "match_loc": "Zaragoza, Aragón",
     "lk": 8, "email_value": "elena.ibarra@example.com"},
    {"label": "candidate 2", "matched_full": "Elena Pons Ruiz", "match_loc": "Valencia",
     "lk": 3, "email_value": "epons@example.org"},
]


def say(msg="", pause=PAUSE):
    print(msg, flush=True)
    time.sleep(pause)


def step(n, title):
    say(f"\n{C['b']}{C['c']}━━ {n}. {title} {'━' * max(3, 58 - len(title))}{C['x']}", 0.8)


def highlight(text):
    for _label, frag in TARGETS:
        text = text.replace(frag, f"{C['hl']}{frag}{C['x']}")
    return text


def wrap(text, width=84):
    import textwrap
    return "\n".join(textwrap.wrap(text, width, replace_whitespace=False))


def main():
    say(f"{C['b']}NadiaAI — one lead, end to end{C['x']}   "
        f"{C['d']}(synthetic data; real code){C['x']}")

    step(1, "The source document (public gazette edict)")
    say(wrap(highlight(EDICT)), 2.5)
    say(f"\n{C['d']}Highlighted = the fields we want out of it:{C['x']}", 0.6)
    for label, frag in TARGETS:
        say(f"  {C['hl']} {label:<22}{C['x']}  {frag}", 0.5)

    step(2, "Deterministic parser — no AI for the templated parts")
    p = parse_notaria_edict(EDICT)
    for k in ("notary", "causante", "heir", "domicile", "protocol"):
        say(f"  {k:<10} {C['g']}{p[k]}{C['x']}", 0.45)
    say(f"{C['d']}  regex only: free, instant, can't hallucinate{C['x']}")

    step(3, "AI extraction — the free-text parts (recorded model output)")
    say(json.dumps(LLM_JSON, indent=2, ensure_ascii=False), 2.2)

    step(4, "Validation — the model proposes, code decides")
    raw = LLM_JSON["list_of_heirs"]
    kept = clean_name_list(raw)
    for name in raw:
        ok = name in kept
        say(f"  {C['g'] + '✓ keep  ' if ok else C['r'] + '✗ reject'}{C['x']}  {name}"
            + ("" if ok else f"   {C['d']}← institution, not a person{C['x']}"), 0.7)
    tier = "A" if any(ch.isdigit() for ch in LLM_JSON["property_address"]) else "B"
    say(f"\n  tier {C['b']}{tier}{C['x']}  "
        f"{C['d']}(street number present → actionable address){C['x']}")

    step(5, "Enrichment — find the heir's email, refuse wrong people")
    heir = kept[0]
    say(f"  looking up: {C['b']}{heir}{C['x']}, Zaragoza   "
        f"{C['d']}(provider replies below are recorded samples){C['x']}", 1.0)
    email = ""
    for cand in CANDIDATES:
        keep, conf = tp._verify_identity(heir, "Zaragoza", "Aragón", cand, coheirs=kept[1:])
        mark = f"{C['g']}✓ ACCEPT [{conf}]" if keep else f"{C['r']}✗ REJECT"
        say(f"  {cand['label']}: {cand['matched_full']:<20} {cand['match_loc']:<18} → {mark}{C['x']}", 1.0)
        if keep and not email:
            email = cand["email_value"]
    say(f"\n  {C['d']}Precision over recall: a wrong person is worse than no email.{C['x']}", 1.2)

    step(6, "Result")
    say(f"  {C['b']}Ramón Ibarra Salas{C['x']}  →  heir {C['b']}{heir}{C['x']}", 0.5)
    say(f"  address   {LLM_JSON['property_address']}", 0.5)
    say(f"  tier      {tier}", 0.5)
    say(f"  email     {C['g']}{email}{C['x']}   {C['d']}(confidence: high){C['x']}", 1.0)


if __name__ == "__main__":
    main()
