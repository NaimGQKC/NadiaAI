"""Tests for export_for_enrichment.py

Covers:
- Tier A+B both included (Tier C/X excluded)
- (name, city) deduplication across leads
- Junk/boilerplate name rejection via is_valid_person_name
- causante fallback also validated
"""

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from nadia_ai.export_for_enrichment import export_csv, get_leads_for_enrichment
from nadia_ai.merge import init_leads_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn() -> sqlite3.Connection:
    """Return an in-memory SQLite connection with the leads schema initialised."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_leads_schema(conn)
    return conn


def _insert_lead(conn: sqlite3.Connection, **kwargs) -> int:
    """Insert a minimal lead row and return its id."""
    defaults = {
        "causante": "JUAN PEREZ GARCIA",
        "heir_name": "",
        "heir_names_json": "[]",
        "localidad": "Zaragoza",
        "direccion": "",
        "tier": "A",
        "outreach_allowed": 1,
        "days_since_death": 10,
        "urgency_phase": "hot",
        "social_profile_url": "",
        # required NOT NULL columns
        "sources": "[]",
        "source_urls": "[]",
    }
    defaults.update(kwargs)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join("?" for _ in defaults)
    cur = conn.execute(
        f"INSERT INTO leads ({cols}) VALUES ({placeholders})",
        list(defaults.values()),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# get_leads_for_enrichment — tier inclusion
# ---------------------------------------------------------------------------

class TestGetLeadsForEnrichment:
    def test_tier_a_included(self):
        conn = _make_conn()
        _insert_lead(conn, tier="A")
        leads = get_leads_for_enrichment(conn)
        assert len(leads) == 1
        assert leads[0]["tier"] == "A"

    def test_tier_b_included(self):
        """Tier B leads were silently excluded before the fix — must now appear."""
        conn = _make_conn()
        _insert_lead(conn, tier="B")
        leads = get_leads_for_enrichment(conn)
        assert len(leads) == 1
        assert leads[0]["tier"] == "B"

    def test_tier_c_excluded(self):
        conn = _make_conn()
        _insert_lead(conn, tier="C")
        leads = get_leads_for_enrichment(conn)
        assert leads == []

    def test_tier_x_excluded(self):
        conn = _make_conn()
        _insert_lead(conn, tier="X")
        leads = get_leads_for_enrichment(conn)
        assert leads == []

    def test_both_tiers_returned_and_a_first(self):
        """ORDER BY should put tier A before tier B."""
        conn = _make_conn()
        _insert_lead(conn, tier="B", days_since_death=5)
        _insert_lead(conn, tier="A", days_since_death=5)
        leads = get_leads_for_enrichment(conn)
        assert len(leads) == 2
        assert leads[0]["tier"] == "A"
        assert leads[1]["tier"] == "B"

    def test_social_profile_url_set_excluded(self):
        """Leads that already have a social profile URL are skipped."""
        conn = _make_conn()
        _insert_lead(conn, tier="A", social_profile_url="https://fb.com/someone")
        leads = get_leads_for_enrichment(conn)
        assert leads == []

    def test_outreach_not_allowed_excluded(self):
        conn = _make_conn()
        _insert_lead(conn, tier="A", outreach_allowed=0)
        leads = get_leads_for_enrichment(conn)
        assert leads == []


# ---------------------------------------------------------------------------
# export_csv — deduplication
# ---------------------------------------------------------------------------

class TestExportCsvDeduplication:
    def _run_export(self, leads: list[dict], tmp_path: Path) -> list[list[str]]:
        """Run export_csv with EXPORTS_DIR overridden to tmp_path; return rows."""
        with patch("nadia_ai.export_for_enrichment.EXPORTS_DIR", tmp_path):
            csv_path = export_csv(leads)
        import csv
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            return list(reader)

    def test_duplicate_name_city_written_once(self, tmp_path):
        """Same heir name + city from two different leads → only one CSV row."""
        leads = [
            {
                "id": 1, "causante": "", "heir_name": "Maria Lopez Sanz",
                "heir_names_json": "[]", "localidad": "Zaragoza",
                "tier": "A", "urgency_phase": "hot",
            },
            {
                "id": 2, "causante": "", "heir_name": "Maria Lopez Sanz",
                "heir_names_json": "[]", "localidad": "Zaragoza",
                "tier": "B", "urgency_phase": "warm",
            },
        ]
        rows = self._run_export(leads, tmp_path)
        # rows[0] is header
        data_rows = rows[1:]
        assert len(data_rows) == 1, f"Expected 1 data row, got {len(data_rows)}: {data_rows}"
        assert data_rows[0][0] == "Maria Lopez Sanz"

    def test_same_name_different_city_both_written(self, tmp_path):
        """Same name but different city = two distinct search slots."""
        leads = [
            {
                "id": 1, "causante": "", "heir_name": "Ana Martinez Gil",
                "heir_names_json": "[]", "localidad": "Zaragoza",
                "tier": "A", "urgency_phase": "hot",
            },
            {
                "id": 2, "causante": "", "heir_name": "Ana Martinez Gil",
                "heir_names_json": "[]", "localidad": "Madrid",
                "tier": "A", "urgency_phase": "hot",
            },
        ]
        rows = self._run_export(leads, tmp_path)
        data_rows = rows[1:]
        assert len(data_rows) == 2

    def test_dedup_is_case_insensitive(self, tmp_path):
        """'JUAN PEREZ' and 'Juan Perez' in same city should collapse to one row."""
        leads = [
            {
                "id": 1, "causante": "", "heir_name": "JUAN PEREZ LOPEZ",
                "heir_names_json": "[]", "localidad": "Zaragoza",
                "tier": "A", "urgency_phase": "hot",
            },
            {
                "id": 2, "causante": "", "heir_name": "Juan Perez Lopez",
                "heir_names_json": "[]", "localidad": "Zaragoza",
                "tier": "B", "urgency_phase": "warm",
            },
        ]
        rows = self._run_export(leads, tmp_path)
        data_rows = rows[1:]
        assert len(data_rows) == 1

    def test_heir_names_json_deduped_against_heir_name(self, tmp_path):
        """heir_names_json listing the same name as heir_name → still one row."""
        leads = [
            {
                "id": 1,
                "causante": "CARLOS RUIZ MORA",
                "heir_name": "Sofia Ruiz Vega",
                "heir_names_json": json.dumps(["Sofia Ruiz Vega", "Pedro Ruiz Vega"]),
                "localidad": "Zaragoza",
                "tier": "A",
                "urgency_phase": "hot",
            },
        ]
        rows = self._run_export(leads, tmp_path)
        data_rows = rows[1:]
        names = [r[0] for r in data_rows]
        # Sofia appears once (heir_name + json duplicate collapsed)
        assert names.count("Sofia Ruiz Vega") == 1
        # Pedro is distinct
        assert "Pedro Ruiz Vega" in names


# ---------------------------------------------------------------------------
# export_csv — name validation / junk rejection
# ---------------------------------------------------------------------------

class TestExportCsvNameValidation:
    def _run_export(self, leads: list[dict], tmp_path: Path) -> list[list[str]]:
        with patch("nadia_ai.export_for_enrichment.EXPORTS_DIR", tmp_path):
            csv_path = export_csv(leads)
        import csv
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            return list(reader)

    def test_junk_heir_name_rejected(self, tmp_path):
        """Boilerplate strings must not appear in the CSV."""
        leads = [
            {
                "id": 1, "causante": "Fallecido Desconocido",
                "heir_name": "HEREDEROS DE",   # junk
                "heir_names_json": "[]",
                "localidad": "Zaragoza",
                "tier": "A", "urgency_phase": "hot",
            },
        ]
        rows = self._run_export(leads, tmp_path)
        # causante 'Fallecido Desconocido' is also junk — row count may be 0
        data_rows = rows[1:]
        names = [r[0] for r in data_rows]
        assert "HEREDEROS DE" not in names

    def test_empty_heir_falls_back_to_causante(self, tmp_path):
        """When heir fields are empty, causante is used (if valid)."""
        leads = [
            {
                "id": 1, "causante": "Luis Fernandez Mora",
                "heir_name": "",
                "heir_names_json": "[]",
                "localidad": "Zaragoza",
                "tier": "A", "urgency_phase": "hot",
            },
        ]
        rows = self._run_export(leads, tmp_path)
        data_rows = rows[1:]
        assert len(data_rows) == 1
        assert data_rows[0][0] == "Luis Fernandez Mora"

    def test_causante_fallback_also_validated(self, tmp_path):
        """Junk causante with no heir → zero rows (not an unvalidated crash path)."""
        leads = [
            {
                "id": 1, "causante": "HEREDEROS",  # junk — should be rejected
                "heir_name": "",
                "heir_names_json": "[]",
                "localidad": "Zaragoza",
                "tier": "B", "urgency_phase": "warm",
            },
        ]
        rows = self._run_export(leads, tmp_path)
        data_rows = rows[1:]
        assert data_rows == [], f"Junk causante slipped through: {data_rows}"

    def test_whitespace_only_name_rejected(self, tmp_path):
        """Whitespace-only names must not appear in output."""
        leads = [
            {
                "id": 1, "causante": "   ",
                "heir_name": "  ",
                "heir_names_json": "[]",
                "localidad": "Zaragoza",
                "tier": "A", "urgency_phase": "hot",
            },
        ]
        rows = self._run_export(leads, tmp_path)
        data_rows = rows[1:]
        assert data_rows == []
