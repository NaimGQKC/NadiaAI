"""Tests for Catastro enrichment pipeline."""

from unittest.mock import patch

from nadia_ai.catastro import _is_cached, enrich_and_persist
from nadia_ai.db import upsert_parcel
from nadia_ai.models import EdictRecord, ParcelInfo


class TestIsCache:
    def test_not_cached_when_missing(self, db_conn):
        assert _is_cached(db_conn, "NONEXISTENT") is False

    def test_not_cached_when_pending(self, db_conn):
        upsert_parcel(
            db_conn,
            {
                "referencia_catastral": "RC001",
                "address": None,
                "neighborhood": None,
                "m2": None,
                "year_built": None,
                "use_class": None,
                "enrichment_pending": 1,
            },
        )
        assert _is_cached(db_conn, "RC001") is False

    def test_cached_when_recent(self, db_conn):
        upsert_parcel(
            db_conn,
            {
                "referencia_catastral": "RC001",
                "address": "CL COSO 15",
                "neighborhood": "Centro",
                "m2": 85.0,
                "year_built": 1960,
                "use_class": "Residencial",
                "enrichment_pending": 0,
            },
        )
        assert _is_cached(db_conn, "RC001") is True


class TestEnrichAndPersist:
    @patch(
        "nadia_ai.catastro.lookup_by_rc",
        return_value=ParcelInfo(
            referencia_catastral="9872301TF6697S0001WX",
            address="CL COSO 15",
            neighborhood="Centro",
            m2=85.0,
            year_built=1960,
            use_class="Residencial",
        ),
    )
    def test_enrich_new_record(self, mock_lookup, db_conn):
        records = [
            EdictRecord(
                source="tablon",
                source_id="E001",
                referencia_catastral="9872301TF6697S0001WX",
                causante="García López",
            )
        ]
        enriched = enrich_and_persist(db_conn, records)
        assert enriched == 1

        # Verify parcel was saved
        row = db_conn.execute(
            "SELECT * FROM parcels WHERE referencia_catastral = '9872301TF6697S0001WX'"
        ).fetchone()
        assert row["address"] == "CL COSO 15"
        assert row["m2"] == 85.0

        # Verify person was saved
        person = db_conn.execute("SELECT * FROM edict_persons WHERE role = 'causante'").fetchone()
        assert person["full_name"] == "García López"

    @patch("nadia_ai.catastro.lookup_by_rc", return_value=None)
    def test_enrich_catastro_failure_flags_pending(self, mock_lookup, db_conn):
        records = [
            EdictRecord(
                source="tablon",
                source_id="E002",
                referencia_catastral="FAILRC0001",
            )
        ]
        enriched = enrich_and_persist(db_conn, records)
        assert enriched == 0

        row = db_conn.execute(
            "SELECT * FROM parcels WHERE referencia_catastral = 'FAILRC0001'"
        ).fetchone()
        assert row["enrichment_pending"] == 1

    def test_enrich_skips_duplicate(self, db_conn):
        """Second call with same record should return 0 (duplicate skipped)."""
        records = [
            EdictRecord(
                source="tablon",
                source_id="E003",
                referencia_catastral="DUPETEST001",
            )
        ]
        with patch("nadia_ai.catastro.lookup_by_rc", return_value=None):
            enrich_and_persist(db_conn, records)
            enriched = enrich_and_persist(db_conn, records)
        assert enriched == 0

    @patch("nadia_ai.catastro.lookup_by_rc", return_value=None)
    def test_enrich_record_without_rc(self, mock_lookup, db_conn):
        """Records without referencia_catastral should be inserted but not enriched."""
        records = [
            EdictRecord(
                source="tablon",
                source_id="E004",
                referencia_catastral=None,
            )
        ]
        enriched = enrich_and_persist(db_conn, records)
        assert enriched == 0
        mock_lookup.assert_not_called()

    @patch(
        "nadia_ai.catastro.lookup_by_rc",
        return_value=ParcelInfo(
            referencia_catastral="RC_PETITIONERS",
            address="Test",
        ),
    )
    def test_enrich_with_petitioners(self, mock_lookup, db_conn):
        records = [
            EdictRecord(
                source="boa",
                source_id="B001",
                referencia_catastral="RC_PETITIONERS",
                causante="Causante Test",
                petitioners=["Heir One", "Heir Two"],
            )
        ]
        enriched = enrich_and_persist(db_conn, records)
        assert enriched == 1

        persons = db_conn.execute("SELECT * FROM edict_persons ORDER BY role").fetchall()
        assert len(persons) == 3  # 1 causante + 2 petitioners
        roles = [p["role"] for p in persons]
        assert "causante" in roles
        assert roles.count("petitioner") == 2
