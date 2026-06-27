"""End-to-end integration: a BOJA/DOGV scraper record must flow cleanly through
edict persistence → merge → lead, with the right subsource and tier — so the new
bulletins don't break the real run on PEDRO.

No network: records carry no referencia_catastral, so Catastro is never called.
"""

from nadia_ai.catastro import enrich_and_persist
from nadia_ai.merge import merge_leads
from nadia_ai.models import EdictRecord


def _records():
    return [
        # A named edict with a mailable street address → should land Tier A.
        EdictRecord(
            source="boja",
            source_id="boja-1",
            edict_type="declaracion_herederos_abintestato",
            source_url="https://www.juntadeandalucia.es/eboja/2026/12/4.html",
            causante="Juan Pérez García",
            address="Calle Sierpes 23, Sevilla",
        ),
        # A name-less record (only a URL) — the scraper couldn't parse the title;
        # it must still create a placeholder lead for the LLM extraction step.
        EdictRecord(
            source="dogv",
            source_id="dogv-1",
            edict_type="declaracion_herederos_abintestato",
            source_url="https://dogv.gva.es/datos/2026/06/27/doc/9.pdf",
            causante=None,
        ),
    ]


def test_boja_dogv_records_flow_to_leads(db_conn):
    records = _records()
    enrich_and_persist(db_conn, records)  # inserts edicts (no RC → no network)
    stats = merge_leads(db_conn, records)

    assert stats["created"] >= 2, f"expected 2 leads, got {stats}"

    rows = {
        r["subsource"]: r
        for r in db_conn.execute(
            "SELECT subsource, causante, tier, direccion, source_urls FROM leads"
        )
    }
    # Subsource codes map correctly.
    assert "BOJA" in rows, f"subsources seen: {list(rows)}"
    assert "DOGV" in rows

    boja = rows["BOJA"]
    assert boja["causante"] == "Juan Pérez García"
    assert boja["tier"] == "A"  # named + street address with number = mailable
    assert "juntadeandalucia.es" in boja["source_urls"]

    # The name-less DOGV record still produced a lead (placeholder for extraction),
    # and its URL is retained so run_heir_extraction can fetch + enrich it.
    dogv = rows["DOGV"]
    assert "dogv.gva.es" in dogv["source_urls"]
    assert dogv["tier"] in ("B", "C")  # no name/address yet → not Tier A
