"""Smoke-test the two configured LLM APIs (extraction + contact search).

Hits each provider once with a tiny prompt (costs a fraction of a cent) and
reports PASS/FAIL with the actual HTTP status/error so misconfiguration is
obvious -- a wrong URL/key/model shows up as a 401/404 here instead of silently
falling back to regex during a real run.

    python tools/test_apis.py
"""

import json
import re
import sys

import requests

from nadia_ai import config


def _mask(secret: str) -> str:
    if not secret:
        return "(empty)"
    return f"{secret[:6]}...{secret[-4:]} (len {len(secret)})"


def _ok(msg):
    print(f"  [PASS] {msg}")


def _fail(msg):
    print(f"  [FAIL] {msg}")


def test_extraction() -> bool:
    print("\n[1/2] Extraction LLM")
    print(f"  url   : {config.EXTRACTION_API_URL}")
    print(f"  model : {config.EXTRACTION_MODEL}")
    print(f"  key   : {_mask(config.EXTRACTION_API_KEY)}")
    if not config.EXTRACTION_API_KEY:
        _fail("EXTRACTION_API_KEY is empty -- set it in .env")
        return False

    sample = (
        "Edicto. En el procedimiento de declaracion de herederos abintestato de "
        "D. JUAN PEREZ GARCIA, vecino de Zaragoza, fallecido el 3 de febrero de "
        "2026, se llama a sus hijos Maria Perez Lopez y Carlos Perez Lopez."
    )
    prompt = (
        "Extrae en JSON: deceased_name, date_of_death (YYYY-MM-DD), list_of_heirs "
        "(array). Responde SOLO el JSON.\n\nTexto:\n" + sample
    )
    body = {
        "model": config.EXTRACTION_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 2000,  # room for reasoning models to think AND emit JSON
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {config.EXTRACTION_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(config.EXTRACTION_API_URL, headers=headers, json=body, timeout=90)
    except requests.RequestException as e:
        _fail(f"network error: {e}")
        return False

    if r.status_code != 200:
        _fail(f"HTTP {r.status_code}: {r.text[:300]}")
        return False

    try:
        choice = r.json()["choices"][0]
        content = choice["message"].get("content")
    except (KeyError, ValueError, IndexError) as e:
        _fail(f"unexpected response shape: {e} -- body: {r.text[:300]}")
        return False

    if not content:
        fr = choice.get("finish_reason")
        _fail(f"empty content (finish_reason={fr}). If 'length', the model ran out "
              f"of tokens on reasoning -- raise max_tokens.")
        return False

    data = None
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
    if not data:
        _fail(f"reply wasn't JSON: {content[:200]}")
        return False

    _ok(f"got JSON: deceased={data.get('deceased_name')!r}, "
        f"date={data.get('date_of_death')!r}, heirs={data.get('list_of_heirs')!r}")

    # Confirm the real pipeline path also works end-to-end.
    from nadia_ai.utils.extraction import extract_inheritance_data
    out = extract_inheritance_data(sample, causante_hint="JUAN PEREZ GARCIA")
    if out.get("list_of_heirs"):
        _ok(f"pipeline extract_inheritance_data() returned heirs: {out['list_of_heirs']}")
    else:
        _fail("pipeline returned no heirs -- extraction may be falling back to regex")
        return False
    return True


def test_perplexity() -> bool:
    print("\n[2/2] Contact-search LLM (Perplexity)")
    print(f"  url   : {config.PERPLEXITY_API_URL}")
    print(f"  model : {config.PERPLEXITY_MODEL}")
    print(f"  key   : {_mask(config.PERPLEXITY_API_KEY)}")
    if not config.PERPLEXITY_API_KEY:
        _fail("PERPLEXITY_API_KEY is empty -- set it in .env")
        return False

    body = {
        "model": config.PERPLEXITY_MODEL,
        "messages": [{"role": "user", "content": "En una frase: que es el Tablon de Edictos?"}],
        "max_tokens": 150,
    }
    headers = {
        "Authorization": f"Bearer {config.PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(config.PERPLEXITY_API_URL, headers=headers, json=body, timeout=40)
    except requests.RequestException as e:
        _fail(f"network error: {e}")
        return False

    if r.status_code != 200:
        _fail(f"HTTP {r.status_code}: {r.text[:300]}")
        return False

    try:
        j = r.json()
        content = j["choices"][0]["message"].get("content")
    except (KeyError, ValueError, IndexError) as e:
        _fail(f"unexpected response shape: {e} -- body: {r.text[:300]}")
        return False

    if not content:
        _fail(f"empty content -- body: {r.text[:200]}")
        return False

    citations = j.get("citations") or [s.get("url") for s in j.get("search_results", []) if s.get("url")]
    _ok(f"replied: {content[:120].strip()}")
    if citations:
        _ok(f"citations returned: {len(citations)} (search grounding works)")
    else:
        print("  note: no citations on this generic question (fine; lead searches will cite)")
    return True


def main():
    print("=" * 64)
    print("NadiaAI API smoke test -- tiny calls, well under $0.01 total")
    print("=" * 64)
    e = test_extraction()
    p = test_perplexity()
    print("\n" + "=" * 64)
    print(f"Extraction: {'PASS' if e else 'FAIL'}   |   Perplexity: {'PASS' if p else 'FAIL'}")
    print("=" * 64)
    sys.exit(0 if (e and p) else 1)


if __name__ == "__main__":
    main()
