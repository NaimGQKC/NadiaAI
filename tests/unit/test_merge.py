"""Tests for lead deduplication, merging, and tier classification.

Phase 2 QA adversarial cases:
- Cross-source dedup produces merged rows
- Tier classification is correct
- Outreach flags work
- Daily delta (first_seen_at) prevents re-emission
- Special characters round-trip cleanly
"""

import json
from datetime import UTC, datetime, timedelta

from nadia_ai.merge import (
    compute_inheritance_window_days,
    compute_outreach,
    compute_tier,
    get_todays_leads,
    merge_leads,
    normalize_address,
    normalize_name,
    province_for_localidad,
    recompute_edict_windows,
)
from nadia_ai.models import EdictRecord


class TestInheritanceWindow:
    def test_open_window_for_recent_notarial(self):
        pub = (datetime.now(UTC) - timedelta(days=5)).isoformat()
        w = compute_inheritance_window_days(pub, "declaracion_herederos_abintestato")
        assert w is not None and 24 <= w <= 26  # 30 - ~5 days elapsed

    def test_closed_window_for_old_edict(self):
        pub = (datetime.now(UTC) - timedelta(days=400)).isoformat()
        assert compute_inheritance_window_days(pub, "inheritance_lead") < 0

    def test_inheritance_lead_cohort_gets_a_window(self):
        # Regression: `inheritance_lead` (BOE-TEJU herencia-yacente + BOE-V.B) is
        # the largest open-window source. The original map keyed an invented
        # `herencia_yacente` that matched zero rows, so this whole cohort silently
        # got None. It must now resolve to an open window.
        pub = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        w = compute_inheritance_window_days(pub, "inheritance_lead")
        assert w is not None and w > 0

    def test_none_for_non_window_edict_types(self):
        now = datetime.now(UTC).isoformat()
        # Obituaries open no claim window — they must stay None even when fresh.
        assert compute_inheritance_window_days(now, "defuncion_esquela") is None
        assert compute_inheritance_window_days(now, "subasta_municipal") is None
        assert compute_inheritance_window_days(now, "traspaso_negocio") is None

    def test_none_for_missing_or_bad_inputs(self):
        assert compute_inheritance_window_days(None, "inheritance_lead") is None
        assert compute_inheritance_window_days("not-a-date", "inheritance_lead") is None

    def test_recompute_picks_most_open_window(self, db_conn):
        # Two linked edicts: an old (closed) and a fresh (open) one — the fresh
        # window must win.
        for sid, days_ago in (("OLD", 200), ("NEW", 3)):
            pub = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
            db_conn.execute(
                "INSERT INTO edicts (source, source_id, edict_type, published_at) VALUES (?,?,?,?)",
                ("boe_n", sid, "declaracion_herederos_abintestato", pub),
            )
        db_conn.execute("INSERT INTO leads (causante) VALUES ('Persona De Prueba')")
        lid = db_conn.execute("SELECT id FROM leads").fetchone()["id"]
        for sid in ("OLD", "NEW"):
            eid = db_conn.execute("SELECT id FROM edicts WHERE source_id=?", (sid,)).fetchone()["id"]
            db_conn.execute("INSERT INTO lead_edicts (lead_id, edict_id) VALUES (?,?)", (lid, eid))
        db_conn.commit()

        n = recompute_edict_windows(db_conn)
        assert n == 1
        w = db_conn.execute("SELECT edict_window_days FROM leads WHERE id=?", (lid,)).fetchone()[0]
        assert 26 <= w <= 28  # from the NEW edict (30 - ~3), not the closed OLD one


class TestProvinceForLocalidad:
    def test_city_is_its_own_province(self):
        assert province_for_localidad("Zaragoza") == "Zaragoza"

    def test_zaragoza_province_towns_roll_up(self):
        for town in ["Calatayud", "Ejea de los Caballeros", "Caspe", "Utebo", "Alagón"]:
            assert province_for_localidad(town) == "Zaragoza", town

    def test_tolerates_province_suffix(self):
        assert province_for_localidad("Ejea de los Caballeros (Zaragoza)") == "Zaragoza"

    def test_non_aragon_keeps_localidad(self):
        assert province_for_localidad("Madrid") == "Madrid"
        assert province_for_localidad("Sevilla") == "Sevilla"

    def test_empty(self):
        assert province_for_localidad("") == ""
        assert province_for_localidad(None) == ""


class TestNormalizeName:
    def test_basic(self):
        assert normalize_name("GARCÍA LÓPEZ MARÍA") == "garcia lopez maria"

    def test_with_honorific(self):
        assert normalize_name("Don Pedro Martínez") == "pedro martinez"
        assert normalize_name("Dña. Ana García") == "ana garcia"

    def test_accents_stripped(self):
        assert normalize_name("José Ángel Muñoz") == "jose angel munoz"

    def test_none_returns_none(self):
        assert normalize_name(None) is None

    def test_empty_returns_none(self):
        assert normalize_name("") is None


class TestNormalizeAddress:
    def test_strips_street_prefix(self):
        assert normalize_address("Calle Alfonso I 15") == "alfonso i 15"

    def test_strips_avda(self):
        assert normalize_address("Avda. Goya 25") == "goya 25"

    def test_short_address_returns_none(self):
        assert normalize_address("CL 1") is None

    def test_none_returns_none(self):
        assert normalize_address(None) is None


class TestComputeTier:
    def test_tier_a_name_and_address(self):
        lead = {
            "causante": "García López",
            "direccion": "Calle Coso 15",
            "sources": '["Tablón"]',
            "first_seen_at": datetime.now(UTC).isoformat(),
        }
        assert compute_tier(lead) == "A"

    def test_tier_b_name_only(self):
        lead = {
            "causante": "García López",
            "direccion": "",
            "sources": '["Tablón"]',
            "first_seen_at": datetime.now(UTC).isoformat(),
        }
        assert compute_tier(lead) == "B"

    def test_tier_b_address_only(self):
        lead = {
            "causante": "",
            "direccion": "Calle Coso 15",
            "sources": '["Tablón"]',
            "first_seen_at": datetime.now(UTC).isoformat(),
        }
        assert compute_tier(lead) == "B"

    def test_tier_c_neither(self):
        lead = {
            "causante": "",
            "direccion": "",
            "sources": '["Tablón"]',
        }
        assert compute_tier(lead) == "C"

    def test_tier_x_distress_subasta(self):
        lead = {
            "causante": "Test",
            "direccion": "Calle Test 1",
            "sources": '["Subastas BOE"]',
        }
        assert compute_tier(lead) == "X"

    def test_tier_x_distress_concurso(self):
        lead = {
            "causante": "Test",
            "direccion": "Calle Test 1",
            "sources": '["Concurso"]',
        }
        assert compute_tier(lead) == "X"

    def test_tier_b_when_stale(self):
        """A record with name+address but >6 months past death gets Tier B."""
        lead = {
            "causante": "García López",
            "direccion": "Calle Coso 15",
            "sources": '["Tablón"]',
            "first_seen_at": datetime.now(UTC).isoformat(),
            "days_since_death": 200,  # Past 180-day deadline
        }
        assert compute_tier(lead) == "B"


class TestComputeOutreach:
    def test_inheritance_allowed(self):
        lead = {"sources": '["Tablón"]'}
        ok, notes = compute_outreach(lead)
        assert ok is True

    def test_distress_blocked(self):
        lead = {"sources": '["Subastas BOE"]'}
        ok, notes = compute_outreach(lead)
        assert ok is False

    def test_borme_b2b_context(self):
        lead = {"sources": '["BORME-I"]'}
        ok, notes = compute_outreach(lead)
        assert ok is True
        assert "B2B" in notes


class TestMergeLeads:
    def test_creates_new_lead(self, db_conn):
        records = [
            EdictRecord(
                source="tablon",
                source_id="T001",
                causante="García López María",
                source_url="https://example.com/1",
            )
        ]
        stats = merge_leads(db_conn, records)
        assert stats["created"] == 1
        assert stats["merged"] == 0

    def test_dedup_by_name(self, db_conn):
        """Same causante from two sources → merged, not duplicated."""
        records1 = [
            EdictRecord(
                source="tablon",
                source_id="T001",
                causante="García López María",
                source_url="https://example.com/1",
            )
        ]
        records2 = [
            EdictRecord(
                source="boa",
                source_id="B001",
                causante="García López María",
                address="Calle Coso 15",
                source_url="https://boa.example.com/1",
            )
        ]
        merge_leads(db_conn, records1)
        stats = merge_leads(db_conn, records2)
        assert stats["merged"] == 1
        assert stats["created"] == 0

        # Verify merged record has both sources
        leads = get_todays_leads(db_conn)
        assert len(leads) == 1
        sources = json.loads(leads[0]["sources"])
        assert "Tablón" in sources
        assert "BOA" in sources
        # Verify address was filled in from BOA
        assert leads[0]["direccion"] == "Calle Coso 15"

    def test_dedup_by_ref_catastral(self, db_conn):
        """Same RC from two sources → merged."""
        records1 = [
            EdictRecord(
                source="tablon",
                source_id="T001",
                causante="Person A",
                referencia_catastral="1234567AB1234C0001DE",
            )
        ]
        records2 = [
            EdictRecord(
                source="boe_secv",
                source_id="BOE-001",
                causante="Person A",
                referencia_catastral="1234567AB1234C0001DE",
                address="Calle Test 5",
            )
        ]
        merge_leads(db_conn, records1)
        stats = merge_leads(db_conn, records2)
        assert stats["merged"] == 1

        leads = get_todays_leads(db_conn)
        assert len(leads) == 1

    def test_skips_empty_records(self, db_conn):
        """Records with no name AND no address are skipped."""
        records = [
            EdictRecord(source="tablon", source_id="T001")
        ]
        stats = merge_leads(db_conn, records)
        assert stats["skipped"] == 1

    def test_tier_assigned_on_create(self, db_conn):
        records = [
            EdictRecord(
                source="tablon",
                source_id="T001",
                causante="García López",
                address="Calle Coso 15",
            )
        ]
        merge_leads(db_conn, records)
        leads = get_todays_leads(db_conn)
        assert leads[0]["tier"] == "A"

    def test_tier_upgrades_on_merge(self, db_conn):
        """Name-only record (Tier B) upgrades to A when address arrives."""
        records1 = [
            EdictRecord(
                source="tablon",
                source_id="T001",
                causante="García López",
            )
        ]
        merge_leads(db_conn, records1)
        leads = get_todays_leads(db_conn)
        assert leads[0]["tier"] == "B"

        records2 = [
            EdictRecord(
                source="boa",
                source_id="B001",
                causante="García López",
                address="Calle Coso 15",
            )
        ]
        merge_leads(db_conn, records2)
        leads = get_todays_leads(db_conn)
        assert leads[0]["tier"] == "A"


class TestDailyDelta:
    def test_only_todays_leads_returned(self, db_conn):
        """Leads from yesterday should NOT appear in today's delta."""
        records = [
            EdictRecord(
                source="tablon",
                source_id="T001",
                causante="Today Lead",
            )
        ]
        merge_leads(db_conn, records)

        # Backdate one lead to yesterday
        db_conn.execute(
            "UPDATE leads SET first_seen_at = datetime('now', '-2 day') WHERE causante = 'Today Lead'"
        )
        db_conn.commit()

        # Add a new lead for today
        records2 = [
            EdictRecord(
                source="tablon",
                source_id="T002",
                causante="New Today Lead",
            )
        ]
        merge_leads(db_conn, records2)

        leads = get_todays_leads(db_conn)
        assert len(leads) == 1
        assert leads[0]["causante"] == "New Today Lead"

    def test_same_edict_next_day_no_duplicate(self, db_conn):
        """Scraping the same edict on two consecutive days produces no duplicate."""
        records = [
            EdictRecord(
                source="tablon",
                source_id="T001",
                causante="Same Person",
            )
        ]
        merge_leads(db_conn, records)

        # "Next day" — same record again
        merge_leads(db_conn, records)

        # Should still be just 1 lead total
        all_leads = db_conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        assert all_leads == 1


class TestSpecialCharacters:
    def test_unicode_roundtrip(self, db_conn):
        """ñ, ç, accented vowels must survive through SQLite and merge."""
        records = [
            EdictRecord(
                source="tablon",
                source_id="T-ñ-001",
                causante="Muñoz Cañizares José Ángel",
                address="Calle Señorío de Molina 3",
                localidad="Zaragoza",
            )
        ]
        merge_leads(db_conn, records)
        leads = get_todays_leads(db_conn)
        assert len(leads) == 1
        assert "ñ" in leads[0]["causante"]
        assert "Á" in leads[0]["causante"]
        assert "ñ" in leads[0]["direccion"]
