"""Tests for the two merge.py bug fixes:

BUG 1: _resolve_date_of_death must include "rememori" in the obituary fallback.
BUG 2: _sanitize_causante must drop junk for person-name sources but keep B2B.
"""

from datetime import UTC, datetime

import pytest

from nadia_ai.merge import _resolve_date_of_death, _sanitize_causante
from nadia_ai.models import EdictRecord


# ── Helper to build minimal EdictRecord ───────────────────────────────────────

def _rec(source: str, **kw) -> EdictRecord:
    return EdictRecord(
        source=source,
        source_id="test-id",
        **kw,
    )


# ── BUG 1: _resolve_date_of_death ─────────────────────────────────────────────


class TestResolveDateOfDeath:
    def test_explicit_date_of_death_takes_priority(self):
        rec = _rec("rememori", date_of_death="2026-01-15", published_at=datetime(2026, 1, 20, tzinfo=UTC))
        assert _resolve_date_of_death(rec) == "2026-01-15"

    def test_fecha_fallecimiento_before_published_at(self):
        rec = _rec("esquelas", fecha_fallecimiento="2026-02-10", published_at=datetime(2026, 2, 12, tzinfo=UTC))
        assert _resolve_date_of_death(rec) == "2026-02-10"

    def test_esquelas_falls_back_to_published_at(self):
        pub = datetime(2026, 3, 5, tzinfo=UTC)
        rec = _rec("esquelas", published_at=pub)
        assert _resolve_date_of_death(rec) == "2026-03-05"

    def test_defunciones_falls_back_to_published_at(self):
        pub = datetime(2026, 4, 7, tzinfo=UTC)
        rec = _rec("defunciones", published_at=pub)
        assert _resolve_date_of_death(rec) == "2026-04-07"

    def test_iesquelas_falls_back_to_published_at(self):
        pub = datetime(2026, 5, 1, tzinfo=UTC)
        rec = _rec("iesquelas", published_at=pub)
        assert _resolve_date_of_death(rec) == "2026-05-01"

    def test_rememori_falls_back_to_published_at(self):
        """BUG 1 fix: rememori was previously missing from the obituary fallback list."""
        pub = datetime(2026, 6, 10, tzinfo=UTC)
        rec = _rec("rememori", published_at=pub)
        assert _resolve_date_of_death(rec) == "2026-06-10"

    def test_rememori_no_published_at_returns_empty(self):
        rec = _rec("rememori")
        assert _resolve_date_of_death(rec) == ""

    def test_legal_source_no_fallback(self):
        """Non-obituary sources (BOE/Tablón) must NOT get a fabricated date."""
        pub = datetime(2026, 6, 10, tzinfo=UTC)
        rec = _rec("boe_teju", published_at=pub)
        assert _resolve_date_of_death(rec) == ""

    def test_tablon_no_fallback(self):
        pub = datetime(2026, 6, 1, tzinfo=UTC)
        rec = _rec("tablon", published_at=pub)
        assert _resolve_date_of_death(rec) == ""


# ── BUG 2: _sanitize_causante ─────────────────────────────────────────────────


class TestSanitizeCausante:
    def test_valid_name_passes_through(self):
        rec = _rec("defunciones", causante="Maria Garcia Lopez")
        assert _sanitize_causante(rec) == "Maria Garcia Lopez"

    def test_single_letter_is_nulled(self):
        """'D' is an honorific fragment — must be dropped."""
        rec = _rec("tablon", causante="D")
        assert _sanitize_causante(rec) is None

    def test_honorific_with_full_name_is_kept(self):
        """'D. Manuel Perez Romero' passes the validator (4 tokens, all word-like)
        so it is kept rather than nulled.  The validator intentionally allows
        initial-plus-dot abbreviations (e.g. J. María García)."""
        rec = _rec("tablon", causante="D. Manuel Perez Romero")
        assert _sanitize_causante(rec) == "D. Manuel Perez Romero"

    def test_prefix_la_joven_is_nulled(self):
        rec = _rec("defunciones", causante="La Joven Sara Garcia Muniz")
        assert _sanitize_causante(rec) is None

    def test_prefix_el_senor_is_nulled(self):
        rec = _rec("defunciones", causante="El Senor Domingo Ramon Ramon")
        assert _sanitize_causante(rec) is None

    def test_too_long_name_is_nulled(self):
        rec = _rec("defunciones", causante="Yolanda Fernandez Caro Rodriguez Fernandez Caro Rodriguez")
        assert _sanitize_causante(rec) is None

    def test_mojibake_name_is_nulled(self):
        rec = _rec("defunciones", causante="Avelina Ferna 769 Ndez Varela")
        assert _sanitize_causante(rec) is None

    def test_none_causante_returns_none(self):
        rec = _rec("esquelas")
        assert _sanitize_causante(rec) is None

    def test_b2b_traspasos_kept_even_if_invalid_name(self):
        """Traspasos company names are NOT person names — must be kept as-is."""
        rec = _rec("traspasos", causante="INDUSTRIAS BIAL S.L.: Carpinteria de aluminio")
        assert _sanitize_causante(rec) == "INDUSTRIAS BIAL S.L.: Carpinteria de aluminio"

    def test_b2b_borme_i_kept(self):
        rec = _rec("borme_i", causante="MERCADONA SA")
        assert _sanitize_causante(rec) == "MERCADONA SA"

    def test_b2b_borme_ii_kept(self):
        rec = _rec("borme_ii", causante="ZARAGOZA HOUSING SL")
        assert _sanitize_causante(rec) == "ZARAGOZA HOUSING SL"

    def test_valid_rememori_name_passes(self):
        rec = _rec("rememori", causante="Jose Luis Martin Sanchez")
        assert _sanitize_causante(rec) == "Jose Luis Martin Sanchez"
