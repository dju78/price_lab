"""Tests that the Alembic migration actually runs and produces the expected
schema and seed data, rather than only being reviewed by eye."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

REPO_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(db_path: Path) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_migration_upgrades_head_cleanly_and_seeds_coicop(tmp_path, monkeypatch):
    db_path = tmp_path / "migration_test.db"
    # env.py reads the URL from Settings, which is env-driven; point it at
    # the same throwaway file so both paths agree on where to migrate.
    monkeypatch.setenv("PRICELAB_DATABASE_URL", f"sqlite:///{db_path}")
    from pricelab.core.config import get_settings
    get_settings.cache_clear()

    command.upgrade(_alembic_config(db_path), "head")

    engine = create_engine(f"sqlite:///{db_path}")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"users", "sessions", "audit_events", "index_runs", "classification_nodes"} <= tables

    with engine.connect() as conn:
        count = conn.execute(text("select count(*) from classification_nodes")).scalar_one()
        assert count == 13
        codes = {row[0] for row in conn.execute(text("select code from classification_nodes"))}
        assert codes == {f"{i:02d}" for i in range(1, 14)}

    index_run_columns = {c["name"] for c in inspector.get_columns("index_runs")}
    assert {"price_reference_period", "weight_reference_period",
            "index_reference_period"} <= index_run_columns

    get_settings.cache_clear()


def test_0002_heals_a_database_that_already_applied_the_pre_edit_0001(tmp_path, monkeypatch):
    """Simulates a database that ran 0001 before it was edited to add the
    reference-period columns directly to it: `index_runs` exists (with a
    real row in it) but lacks those columns, and `alembic_version` already
    records "0001". Migration 0002 must heal it -- add the missing columns
    without erroring and without losing the existing row -- rather than
    the schema staying silently stale until some future query hits a bare
    OperationalError naming a column nobody expected to be missing."""
    db_path = tmp_path / "pre_edit_0001.db"
    monkeypatch.setenv("PRICELAB_DATABASE_URL", f"sqlite:///{db_path}")
    from pricelab.core.config import get_settings
    get_settings.cache_clear()

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE index_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id VARCHAR(32) NOT NULL UNIQUE,
                input_hash VARCHAR(64) NOT NULL,
                content_hash VARCHAR(64) NOT NULL,
                config_json TEXT NOT NULL,
                code_version VARCHAR(64) NOT NULL,
                environment_fingerprint TEXT NOT NULL,
                label VARCHAR(255) NOT NULL,
                created_at VARCHAR(32) NOT NULL,
                approved BOOLEAN NOT NULL DEFAULT 0,
                vintage INTEGER NOT NULL DEFAULT 1,
                correction_reason TEXT,
                supersedes_run_id VARCHAR(32),
                input_parquet BLOB NOT NULL
            )
        """))
        conn.execute(text("""
            INSERT INTO index_runs (run_id, input_hash, content_hash, config_json,
                code_version, environment_fingerprint, label, created_at, input_parquet)
            VALUES ('abc123', 'hash1', 'hash2', '{}', 'v1', 'env1', 'old run',
                    '2025-01-01', X'00')
        """))

    cfg = _alembic_config(db_path)
    command.stamp(cfg, "0001")  # record that the pre-edit shape of 0001 already ran

    command.upgrade(cfg, "head")  # must not raise, and must add the missing columns

    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("index_runs")}
    assert {"price_reference_period", "weight_reference_period",
            "index_reference_period"} <= columns

    with engine.connect() as conn:
        row = conn.execute(
            text("select run_id, price_reference_period from index_runs")).fetchone()
        assert row is not None
        assert row[0] == "abc123"        # the pre-existing row survived the healing
        assert row[1] is None            # backfilled as unknown, not fabricated

    get_settings.cache_clear()


def test_migration_downgrade_removes_every_table(tmp_path, monkeypatch):
    db_path = tmp_path / "migration_downgrade_test.db"
    monkeypatch.setenv("PRICELAB_DATABASE_URL", f"sqlite:///{db_path}")
    from pricelab.core.config import get_settings
    get_settings.cache_clear()

    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    engine = create_engine(f"sqlite:///{db_path}")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names()) - {"alembic_version"}
    assert tables == set()

    get_settings.cache_clear()
