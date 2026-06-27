"""run_heir_extraction parallel path: network in threads, DB writes serial.

Verifies the refactor (EXTRACTION_WORKERS) enriches leads correctly end-to-end
against a real in-memory DB, with the LLM + PDF fetch mocked.
"""

import json
from unittest import mock

import nadia_ai.utils.extraction as ex


def _seed_lead(conn, lead_id, url="https://www.boe.es/x/not.php?id=BOE-N-1"):
    conn.execute(
        "INSERT INTO leads (id, causante, sources, source_urls, ai_extraction_done) "
        "VALUES (?, ?, ?, ?, 0)",
        (lead_id, "Juan Pérez García", json.dumps(["BOE Notarial"]), json.dumps([url])),
    )
    conn.commit()


def test_parallel_extraction_enriches_leads(db_conn, monkeypatch):
    for i in (1, 2, 3):
        _seed_lead(db_conn, i)

    monkeypatch.setenv("EXTRACTION_WORKERS", "3")
    fake_llm = {
        "deceased_name": "Juan Pérez García",
        "date_of_death": "2026-05-01",
        "list_of_heirs": ["María Pérez López"],
        "property_address": "Calle Mayor 5, Madrid",   # has a number → Tier A
        "referencia_catastral": None,
    }
    with mock.patch.object(ex, "extract_pdf_text", return_value=(
        "Declaración de herederos abintestato del causante Juan Pérez García, "
        "fallecido, con último domicilio en Calle Mayor 5, Madrid.")), \
         mock.patch.object(ex, "_extract_via_llm", return_value=fake_llm):
        enriched = ex.run_heir_extraction(db_conn, limit=10)

    assert enriched == 3
    rows = db_conn.execute(
        "SELECT tier, heir_name, ai_extraction_done FROM leads ORDER BY id"
    ).fetchall()
    for r in rows:
        assert r["ai_extraction_done"] == 1
        assert r["tier"] == "A"             # mailable street address
        assert r["heir_name"] == "María Pérez López"


def test_worker_returns_done_for_obituary_only(db_conn):
    # Obituary-only lead must be marked done without an LLM call.
    db_conn.execute(
        "INSERT INTO leads (id, causante, sources, source_urls, ai_extraction_done) "
        "VALUES (99, '', ?, ?, 0)",
        (json.dumps(["Esquelas"]), json.dumps(["https://memora.es/x"])),
    )
    db_conn.commit()
    action = ex._extract_lead_payload(dict(db_conn.execute(
        "SELECT * FROM leads WHERE id = 99").fetchone()))
    assert action == ("done", 99)
