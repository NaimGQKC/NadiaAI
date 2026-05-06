"""Tests for Pydantic models."""

from datetime import UTC, datetime

from nadia_ai.models import EdictRecord, LeadRow, ParcelInfo


class TestEdictRecord:
    def test_minimal_creation(self):
        record = EdictRecord(source="tablon", source_id="abc123")
        assert record.source == "tablon"
        assert record.source_id == "abc123"
        assert record.referencia_catastral is None
        assert record.edict_type == "declaracion_herederos_abintestato"
        assert record.petitioners == []

    def test_full_creation(self):
        record = EdictRecord(
            source="boa",
            source_id="boa-2026-001",
            referencia_catastral="1234567AB1234C0001DE",
            edict_type="junta_distribuidora_herencias",
            published_at=datetime(2026, 4, 15, tzinfo=UTC),
            source_url="https://boa.aragon.es/test",
            causante="García López María",
            petitioners=["García Pérez Juan", "García Pérez Ana"],
        )
        assert record.referencia_catastral == "1234567AB1234C0001DE"
        assert record.causante == "García López María"
        assert len(record.petitioners) == 2

    def test_unicode_names(self):
        """Spanish characters must survive round-trip."""
        record = EdictRecord(
            source="tablon",
            source_id="test-ñ",
            causante="Muñoz Cañizares José Ángel",
        )
        assert "ñ" in record.source_id
        assert "ñ" in record.causante
        assert "Á" in record.causante


class TestParcelInfo:
    def test_basic(self):
        info = ParcelInfo(referencia_catastral="1234567AB1234C0001DE")
        assert info.m2 is None
        assert info.address == ""

    def test_full(self):
        info = ParcelInfo(
            referencia_catastral="1234567AB1234C0001DE",
            address="CL COSO 15, ZARAGOZA",
            neighborhood="Casco Histórico",
            m2=85.5,
            year_built=1960,
            use_class="Residencial",
        )
        assert info.m2 == 85.5
        assert info.neighborhood == "Casco Histórico"


class TestLeadRow:
    def test_to_row(self):
        lead = LeadRow(
            tier="A",
            fecha_deteccion="2026-04-28",
            fuentes="Tablón",
            causante="García López María",
            localidad="Zaragoza",
            referencia_catastral="1234567AB1234C0001DE",
            direccion="CL COSO 15",
            m2=85.5,
            tipo_inmueble="Residencial",
        )
        row = lead.to_row()
        assert len(row) == 17
        assert row[0] == "A"  # tier
        assert row[1] == "2026-04-28"
        assert row[2] == "Tablón"  # fuentes
        assert row[3] == "García López María"
        assert row[5] == "Zaragoza"
        assert row[10] == "Nuevo"  # default estado
        assert row[15] == ""  # subasta_activa (empty default)
        assert row[16] == ""  # obras_recientes (empty default)

    def test_to_row_with_empty_values(self):
        lead = LeadRow(
            tier="B",
            fecha_deteccion="2026-04-28",
            fuentes="BOA",
            causante="Test Person",
        )
        row = lead.to_row()
        assert row[7] == ""  # referencia_catastral
        assert row[8] == ""  # m2 is None

    def test_sheet_headers(self):
        headers = LeadRow.sheet_headers()
        assert len(headers) == 17
        assert headers[0] == "Tier"
        assert headers[3] == "Causante"
        assert headers[8] == "m²"
        assert "Dirección" in headers
        assert "Subasta activa" in headers
        assert "Obras recientes" in headers

    def test_causante_is_primary_lead_info(self):
        """Causante (deceased) name is the primary lead identifier."""
        lead = LeadRow(
            tier="B",
            fecha_deteccion="2026-04-28",
            fuentes="Tablón",
            causante="García López María",
        )
        row = lead.to_row()
        assert row[3] == "García López María"
