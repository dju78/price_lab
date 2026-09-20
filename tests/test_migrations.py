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
