"""Tests for database operations."""

from datetime import UTC, datetime, timedelta

from nadia_ai.db import (
    get_todays_leads,
    insert_edict,
    person_expiry_date,
    purge_expired_persons,
    upsert_parcel,
)


class TestSchema:
    def test_tables_exist(self, db_conn):
        tables = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = [t["name"] for t in tables]
        assert "parcels" in table_names
        assert "edicts" in table_names
        assert "edict_persons" in table_names

    def test_foreign_keys_enabled(self, db_conn):
        result = db_conn.execute("PRAGMA foreign_keys").fetchone()
        assert result[0] == 1


class TestUpsertParcel:
    def test_insert_new(self, db_conn):
        upsert_parcel(
            db_conn,
            {
                "referencia_catastral": "TEST001",
                "address": "CL COSO 15",
                "neighborhood": "Casco Histórico",
                "m2": 85.5,
                "year_built": 1960,
                "use_class": "Residencial",
                "enrichment_pending": 0,
            },
        )
        row = db_conn.execute(
            "SELECT * FROM parcels WHERE referencia_catastral = 'TEST001'"
        ).fetchone()
        assert row["address"] == "CL COSO 15"
        assert row["m2"] == 85.5

    def test_upsert_updates_existing(self, db_conn):
        upsert_parcel(
            db_conn,
            {
                "referencia_catastral": "TEST001",
                "address": None,
                "neighborhood": None,
                "m2": None,
                "year_built": None,
                "use_class": None,
                "enrichment_pending": 1,
            },
        )
        upsert_parcel(
            db_conn,
            {
                "referencia_catastral": "TEST001",
                "address": "CL COSO 15",
                "neighborhood": "Centro",
                "m2": 90.0,
                "year_built": 1960,
                "use_class": "Residencial",
                "enrichment_pending": 0,
            },
        )
        row = db_conn.execute(
            "SELECT * FROM parcels WHERE referencia_catastral = 'TEST001'"
        ).fetchone()
        assert row["address"] == "CL COSO 15"
        assert row["enrichment_pending"] == 0

    def test_upsert_preserves_existing_on_null(self, db_conn):
        """COALESCE should keep existing data when new value is NULL."""
        upsert_parcel(
            db_conn,
            {
                "referencia_catastral": "TEST001",
                "address": "CL COSO 15",
                "neighborhood": "Centro",
                "m2": 90.0,
                "year_built": 1960,
                "use_class": "Residencial",
                "enrichment_pending": 0,
            },
        )
        upsert_parcel(
            db_conn,
            {
                "referencia_catastral": "TEST001",
                "address": None,
                "neighborhood": None,
                "m2": None,
                "year_built": None,
                "use_class": None,
                "enrichment_pending": 1,
            },
        )
        row = db_conn.execute(
            "SELECT * FROM parcels WHERE referencia_catastral = 'TEST001'"
        ).fetchone()
        assert row["address"] == "CL COSO 15"  # preserved


class TestInsertEdict:
    def test_insert_new(self, db_conn):
        # Need a parcel first for FK
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
        edict_id = insert_edict(
            db_conn,
            {
                "source": "tablon",
                "source_id": "EDI001",
                "referencia_catastral": "RC001",
                "edict_type": "declaracion_herederos_abintestato",
                "published_at": "2026-04-28T00:00:00",
                "source_url": "https://example.com/edicto/1",
                "address": None,
            },
        )
        assert edict_id is not None

    def test_duplicate_returns_none(self, db_conn):
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
        data = {
            "source": "tablon",
            "source_id": "EDI001",
            "referencia_catastral": "RC001",
            "edict_type": "declaracion_herederos_abintestato",
            "published_at": None,
            "source_url": "",
            "address": None,
        }
        insert_edict(db_conn, data)
        result = insert_edict(db_conn, data)
        assert result is None  # duplicate


class TestPersonTTL:
    def test_person_expiry_in_future(self):
        expiry = person_expiry_date()
        expiry_dt = datetime.fromisoformat(expiry)
        assert expiry_dt > datetime.now(UTC)

    def test_purge_expired(self, db_conn):
        # Insert a parcel and edict first
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
        edict_id = insert_edict(
            db_conn,
            {
                "source": "tablon",
                "source_id": "E001",
                "referencia_catastral": "RC001",
                "edict_type": "test",
                "published_at": None,
                "source_url": "",
                "address": None,
            },
        )

        # Insert an expired person
        past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        db_conn.execute(
            "INSERT INTO edict_persons (edict_id, role, full_name, expires_at) VALUES (?, ?, ?, ?)",
            (edict_id, "causante", "Test Person", past),
        )
        db_conn.commit()

        count = purge_expired_persons(db_conn)
        assert count == 1

        remaining = db_conn.execute("SELECT COUNT(*) FROM edict_persons").fetchone()[0]
        assert remaining == 0

    def test_purge_keeps_valid(self, db_conn):
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
        edict_id = insert_edict(
            db_conn,
            {
                "source": "tablon",
                "source_id": "E001",
                "referencia_catastral": "RC001",
                "edict_type": "test",
                "published_at": None,
                "source_url": "",
                "address": None,
            },
        )

        future = (datetime.now(UTC) + timedelta(days=365)).isoformat()
        db_conn.execute(
            "INSERT INTO edict_persons (edict_id, role, full_name, expires_at) VALUES (?, ?, ?, ?)",
            (edict_id, "causante", "Valid Person", future),
        )
        db_conn.commit()

        count = purge_expired_persons(db_conn)
        assert count == 0

        remaining = db_conn.execute("SELECT COUNT(*) FROM edict_persons").fetchone()[0]
        assert remaining == 1


class TestGetTodaysLeads:
    def test_empty_db(self, db_conn):
        leads = get_todays_leads(db_conn)
        assert leads == []
