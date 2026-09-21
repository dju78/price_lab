"""Which database the suite runs against.

By default every test that needs a database gets its own throwaway
SQLite file under pytest's tmp_path. Set `PRICELAB_TEST_DATABASE_URL` to
a PostgreSQL URL and the same tests run against that server instead,
each test starting from an empty schema (`public` is dropped and
recreated before the test), which is how the suite is run against the
production database named in the specification:

    PRICELAB_TEST_DATABASE_URL=postgresql+psycopg://user@host:5432/pricelab_test pytest

Tests that are about SQLite itself (the file-level backup, the SQLite
statement-timeout mechanism) say so and skip under the override.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

OVERRIDE = os.environ.get("PRICELAB_TEST_DATABASE_URL", "").strip() or None


def database_url(tmp_path: Path, name: str) -> str:
    """The URL a fixture should point PRICELAB_DATABASE_URL at."""
    if OVERRIDE:
        wipe(OVERRIDE)
        return OVERRIDE
    return f"sqlite:///{tmp_path / name}"


def wipe(url: str) -> None:
    """Empty a PostgreSQL database completely, alembic_version included."""
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()


def is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def sqlite_only(reason: str) -> None:
    """Skip the calling test under a non-SQLite override."""
    if OVERRIDE and not is_sqlite(OVERRIDE):
        pytest.skip(f"SQLite-only by design: {reason}")


def postgres_only() -> None:
    if not OVERRIDE or not OVERRIDE.startswith("postgresql"):
        pytest.skip("needs PRICELAB_TEST_DATABASE_URL pointing at PostgreSQL")
