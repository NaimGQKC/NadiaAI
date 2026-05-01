"""Tests for delivery module — Sheets output and email."""

from unittest.mock import patch

from nadia_ai.db import insert_edict, insert_person, upsert_parcel
from nadia_ai.delivery import (
    compute_todays_leads,
    send_email,
    write_csv_fallback,
)
from nadia_ai.merge import merge_leads
from nadia_ai.models import EdictRecord, LeadRow


class TestComputeTodaysLeads:
    def test_empty_db(self, db_conn):
        leads = compute_todays_leads(db_conn)
        assert leads == []

    def test_with_recent_edict(self, db_conn):
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
        insert_edict(
            db_conn,
            {
                "source": "tablon",
                "source_id": "E001",
                "referencia_catastral": "RC001",
                "edict_type": "declaracion_herederos_abintestato",
                "published_at": None,
                "source_url": "https://example.com/1",
                "address": None,
            },
        )

        # Merge through the pipeline
        records = [
            EdictRecord(
                source="tablon",
                source_id="E001",
                referencia_catastral="RC001",
                causante="García López",
                source_url="https://example.com/1",
            )
        ]
        merge_leads(db_conn, records)

        leads = compute_todays_leads(db_conn)
        assert len(leads) == 1
        assert leads[0].causante == "García López"
        assert "Tablón" in leads[0].fuentes

    def test_boa_source_label(self, db_conn):
        upsert_parcel(
            db_conn,
            {
                "referencia_catastral": "RC002",
                "address": None,
                "neighborhood": None,
                "m2": None,
                "year_built": None,
                "use_class": None,
                "enrichment_pending": 1,
            },
        )
        insert_edict(
            db_conn,
            {
                "source": "boa",
                "source_id": "B001",
                "referencia_catastral": "RC002",
                "edict_type": "junta_distribuidora",
                "published_at": None,
                "source_url": "",
                "address": None,
            },
        )

        records = [
            EdictRecord(
                source="boa",
                source_id="B001",
                referencia_catastral="RC002",
                causante="Test Person",
            )
        ]
        merge_leads(db_conn, records)

        leads = compute_todays_leads(db_conn)
        assert len(leads) == 1
        assert "BOA" in leads[0].fuentes


class TestWriteCsvFallback:
    def test_creates_csv(self, tmp_path):
        leads = [
            LeadRow(
                tier="B",
                fecha_deteccion="2026-04-28",
                fuentes="Tablón",
                causante="García López",
                localidad="Zaragoza",
            ),
        ]
        with patch("nadia_ai.delivery.Path") as mock_path:
            mock_path.return_value = tmp_path / "leads_test.csv"
            path = write_csv_fallback(leads)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "García López" in content
        assert "Fecha detección" in content


class TestSendEmail:
    @patch("nadia_ai.delivery.MAMA_EMAIL", "test@example.com")
    @patch("nadia_ai.delivery.SMTP_USER", "sender@gmail.com")
    @patch("nadia_ai.delivery.SMTP_PASSWORD", "password123")
    @patch("nadia_ai.delivery.LEADS_SHEET_ID", "sheet123")
    @patch("nadia_ai.delivery._send_smtp")
    def test_sends_zero_leads_email(self, mock_smtp):
        send_email([], sheet_ok=True)
        mock_smtp.assert_called_once()
        args = mock_smtp.call_args
        assert "sin novedades" in args[0][1].lower()
        assert args[0][0] == "test@example.com"

    @patch("nadia_ai.delivery.MAMA_EMAIL", "test@example.com")
    @patch("nadia_ai.delivery.SMTP_USER", "sender@gmail.com")
    @patch("nadia_ai.delivery.SMTP_PASSWORD", "password123")
    @patch("nadia_ai.delivery.LEADS_SHEET_ID", "sheet123")
    @patch("nadia_ai.delivery._send_smtp")
    def test_sends_leads_email(self, mock_smtp):
        leads = [
            LeadRow(
                tier="A",
                fecha_deteccion="2026-04-28",
                fuentes="Tablón",
                causante="García López María",
                direccion="Calle Test 1",
            ),
            LeadRow(
                tier="B",
                fecha_deteccion="2026-04-28",
                fuentes="BOA",
                causante="Pérez Sánchez Juan",
            ),
        ]
        send_email(leads, sheet_ok=True)
        mock_smtp.assert_called_once()
        subject = mock_smtp.call_args[0][1]
        assert "1 nuevos accionables" in subject
        assert "2 totales" in subject
