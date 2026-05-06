"""Tests for the pipeline orchestrator."""

from unittest.mock import MagicMock, patch

from nadia_ai.run import run_pipeline


class TestRunPipeline:
    @patch("nadia_ai.run.get_connection")
    @patch("nadia_ai.run.init_db")
    @patch("nadia_ai.run.purge_expired_persons", return_value=0)
    def test_pipeline_runs_with_all_scrapers_failing(self, mock_purge, mock_init, mock_conn):
        """Pipeline should complete even if all scrapers fail."""
        mock_conn.return_value = MagicMock()

        with (
            patch(
                "nadia_ai.scrapers.tablon.scrape_tablon",
                side_effect=Exception("Tablón down"),
            ),
            patch(
                "nadia_ai.scrapers.boa.scrape_boa",
                side_effect=Exception("BOA down"),
            ),
            patch(
                "nadia_ai.scrapers.bop.scrape_bop",
                side_effect=Exception("BOP down"),
            ),
            patch(
                "nadia_ai.scrapers.boe.scrape_boe",
                side_effect=Exception("BOE down"),
            ),
            patch(
                "nadia_ai.scrapers.borme.scrape_borme",
                side_effect=Exception("BORME down"),
            ),
            patch("nadia_ai.delivery.compute_todays_leads", return_value=[]),
            patch("nadia_ai.delivery.deliver"),
        ):
            summary = run_pipeline()

        assert summary["tablon_new"] == 0
        assert summary["boa_new"] == 0
        assert summary["bop_new"] == 0
        assert summary["boe_new"] == 0
        assert summary["borme_new"] == 0
        assert len(summary["errors"]) >= 5
        assert "elapsed_seconds" in summary

    @patch("nadia_ai.run.get_connection")
    @patch("nadia_ai.run.init_db")
    @patch("nadia_ai.run.purge_expired_persons", return_value=3)
    def test_pipeline_runs_with_no_leads(self, mock_purge, mock_init, mock_conn):
        """Pipeline runs normally with zero leads."""
        mock_conn.return_value = MagicMock()

        with (
            patch("nadia_ai.scrapers.tablon.scrape_tablon", return_value=[]),
            patch("nadia_ai.scrapers.boa.scrape_boa", return_value=[]),
            patch("nadia_ai.scrapers.bop.scrape_bop", return_value=[]),
            patch("nadia_ai.scrapers.boe.scrape_boe", return_value=[]),
            patch("nadia_ai.scrapers.borme.scrape_borme", return_value=[]),
            patch("nadia_ai.catastro.enrich_and_persist", return_value=0),
            patch("nadia_ai.delivery.compute_todays_leads", return_value=[]),
            patch("nadia_ai.delivery.deliver"),
        ):
            summary = run_pipeline()

        assert summary["purged_persons"] == 3
        assert summary["leads_today"] == 0
        assert summary["errors"] == []


class TestDeliver:
    @patch("nadia_ai.delivery.GOOGLE_SERVICE_ACCOUNT_JSON", "")
    @patch("nadia_ai.delivery.LEADS_SHEET_ID", "")
    @patch("nadia_ai.delivery.MAMA_EMAIL", "")
    @patch("nadia_ai.delivery.SMTP_USER", "")
    @patch("nadia_ai.delivery.SMTP_PASSWORD", "")
    def test_deliver_with_no_config(self):
        """Deliver should not crash when nothing is configured."""
        from nadia_ai.delivery import deliver

        summary = {"errors": []}
        deliver([], summary)
        # No crash, just warnings in logs

    @patch("nadia_ai.delivery.GOOGLE_SERVICE_ACCOUNT_JSON", "fake.json")
    @patch("nadia_ai.delivery.LEADS_SHEET_ID", "sheet123")
    @patch("nadia_ai.delivery.MAMA_EMAIL", "")
    @patch("nadia_ai.delivery.write_to_sheets", side_effect=Exception("Auth failed"))
    def test_deliver_sheets_failure_falls_back_to_csv(self, mock_sheets):
        """When Sheets fails, should write CSV fallback."""
        from nadia_ai.delivery import deliver
        from nadia_ai.models import LeadRow

        leads = [
            LeadRow(
                fecha_deteccion="2026-04-28",
                fuentes="Tablón",
                referencia_catastral="RC001",
            )
        ]
        summary = {"errors": []}

        with patch("nadia_ai.delivery.write_csv_fallback") as mock_csv:
            mock_csv.return_value = "leads_2026-04-28.csv"
            deliver(leads, summary)

        mock_csv.assert_called_once()
        assert any("sheets" in e for e in summary["errors"])
