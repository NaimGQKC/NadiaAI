"""Shared test fixtures."""

import sqlite3
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def db_conn():
    """In-memory SQLite database for testing."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row

    from nadia_ai.db import init_db
    from nadia_ai.merge import init_leads_schema

    init_db(conn)
    init_leads_schema(conn)

    yield conn
    conn.close()


@pytest.fixture
def tablon_fixtures():
    """Path to Tablón HTML fixtures."""
    return FIXTURES_DIR / "tablon"


@pytest.fixture
def boa_fixtures():
    """Path to BOA HTML fixtures."""
    return FIXTURES_DIR / "boa"
