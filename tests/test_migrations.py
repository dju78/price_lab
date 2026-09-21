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
    assert {"users", "sessions", "audit_events", "index_runs", "classification_nodes",
            "validation_overrides", "column_mappings", "quality_adjustments"} <= tables

    # 0005 (Phase 4): the quality adjustment ledger's approval record.
    qa_columns = {c["name"] for c in inspector.get_columns("quality_adjustments")}
    assert qa_columns == {
        "id", "content_hash", "category", "old_item", "new_item", "period", "method",
        "quality_ratio", "parameters_json", "justification", "approved_by", "approved_at",
        "withdrawn_at", "withdrawn_by", "withdrawal_reason"}
    assert any(ix["column_names"] == ["content_hash"]
               for ix in inspector.get_indexes("quality_adjustments"))

    mapping_columns = {c["name"] for c in inspector.get_columns("column_mappings")}
    assert mapping_columns == {"id", "file_hash", "schema_json", "confirmed_by", "confirmed_at"}
    override_columns = {c["name"] for c in inspector.get_columns("validation_overrides")}
    assert override_columns == {
        "id", "content_hash", "dimension", "decision", "reason", "actor", "created_at"}

    with engine.connect() as conn:
        # 0001 seeds the 13 household-consumption divisions; 0003 (Phase 2)
        # loads the complete tree -- 871 codes across all 15 divisions
        # (household plus NPISH and government consumption) down to
        # sub-class level -- from the vendored, source-derived CSV.
        count = conn.execute(text("select count(*) from classification_nodes")).scalar_one()
        assert count == 871
        divisions = {
            row[0] for row in
            conn.execute(text("select code from classification_nodes where level = 0"))}
        assert divisions == {f"{i:02d}" for i in range(1, 16)}

    index_run_columns = {c["name"] for c in inspector.get_columns("index_runs")}
    assert {"price_reference_period", "weight_reference_period",
            "index_reference_period"} <= index_run_columns
    # 0006 (Phase 10): the registered headline and the data vintage.
    assert {"headline_series", "headline_period", "headline_value", "headline_reference_period",
            "data_source", "data_received_at"} <= index_run_columns

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
        # A real database that ran 0001 (any shape of it) also has this
        # table, seeded with the 13 divisions; 0003 must find it and add
        # the rest of the tree to it, not assume it is starting from
        # nothing.
        conn.execute(text("""
            CREATE TABLE classification_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scheme VARCHAR(32) NOT NULL DEFAULT 'COICOP2018',
                code VARCHAR(32) NOT NULL,
                label VARCHAR(255) NOT NULL,
                level INTEGER NOT NULL,
                parent_code VARCHAR(32)
            )
        """))
        conn.execute(text("""
            INSERT INTO classification_nodes (scheme, code, label, level, parent_code)
            VALUES ('COICOP2018', '01', 'Food and non-alcoholic beverages', 0, NULL)
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

        # 0003 also ran (0001 -> 0002 -> 0003 in one `upgrade head` call):
        # the pre-existing division row was not duplicated, and the rest
        # of the tree was added around it.
        count = conn.execute(text("select count(*) from classification_nodes")).scalar_one()
        assert count == 871
        dup = conn.execute(
            text("select count(*) from classification_nodes where code = '01'")).scalar_one()
        assert dup == 1

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
