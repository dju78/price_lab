"""Tests for the index run registry: registration, idempotency, approval,
correction and reproduction."""

import pandas as pd
import pytest

from pricelab.core import db, registry
from pricelab.core.config import RunConfig, get_settings


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", f"sqlite:///{tmp_path / 'registry_test.db'}")
    get_settings.cache_clear()
    db.reset_db_state()
    db.init_db()
    yield
    db.reset_db_state()
    get_settings.cache_clear()


def _collection():
    return pd.DataFrame({
        "period": pd.to_datetime(["2020-01-01", "2020-02-01"] * 2),
        "category": ["Bread", "Bread", "Milk", "Milk"],
        "item_id": ["1", "1", "2", "2"],
        "item_name": ["White", "White", "Semi", "Semi"],
        "price_reported": [1.0, 1.1, 0.9, 0.95],
    })


def test_register_run_records_hash_version_and_environment(fresh_db):
    df = _collection()
    cfg = RunConfig(label="test")
    with db.session_scope() as s:
        run = registry.register_run(s, df, cfg, "test")
        assert run.run_id
        assert run.input_hash
        assert run.content_hash.startswith(run.run_id)
        assert run.code_version  # "unknown" is acceptable outside git, but must be set
        assert "python-" in run.environment_fingerprint
        assert run.approved is False
        assert run.vintage == 1


def test_registering_identical_input_and_config_is_idempotent(fresh_db):
    df = _collection()
    cfg = RunConfig(label="test")
    with db.session_scope() as s:
        first = registry.register_run(s, df, cfg, "test")
        first_id = first.run_id
    with db.session_scope() as s:
        second = registry.register_run(s, df, cfg, "test")
        assert second.run_id == first_id
        assert s.query(registry.IndexRunORM).count() == 1


def test_different_config_registers_a_different_run(fresh_db):
    df = _collection()
    with db.session_scope() as s:
        a = registry.register_run(s, df, RunConfig(label="a", index={"formula": "jevons"}), "a")
        b = registry.register_run(s, df, RunConfig(label="a", index={"formula": "dutot"}), "a")
        assert a.run_id != b.run_id


def test_reproduce_returns_the_same_index_series(fresh_db):
    df = _collection()
    cfg = RunConfig(label="test")
    with db.session_scope() as s:
        run_id = registry.register_run(s, df, cfg, "test").run_id
    with db.session_scope() as s:
        reproduced = registry.reproduce(s, run_id)
    from pricelab import run_pipeline
    live = run_pipeline(df, cfg)
    pd.testing.assert_frame_equal(reproduced["indices"], live["indices"])


def test_approved_run_cannot_be_corrected_without_a_reason(fresh_db):
    df = _collection()
    cfg = RunConfig(label="test")
    with db.session_scope() as s:
        run_id = registry.register_run(s, df, cfg, "test").run_id
        registry.approve_run(s, run_id)

    df2 = df.copy()
    df2.loc[0, "price_reported"] = 5.0
    with db.session_scope() as s:
        with pytest.raises(ValueError, match="reason"):
            registry.correct_run(s, run_id, df2, cfg, "test", reason="")


def test_correcting_an_unapproved_run_is_refused(fresh_db):
    df = _collection()
    cfg = RunConfig(label="test")
    with db.session_scope() as s:
        run_id = registry.register_run(s, df, cfg, "test").run_id

    df2 = df.copy()
    df2.loc[0, "price_reported"] = 5.0
    with db.session_scope() as s:
        with pytest.raises(ValueError, match="not approved"):
            registry.correct_run(s, run_id, df2, cfg, "test", reason="oops")


def test_correction_creates_a_new_vintage_and_leaves_the_original_untouched(fresh_db):
    df = _collection()
    cfg = RunConfig(label="test")
    with db.session_scope() as s:
        run_id = registry.register_run(s, df, cfg, "test").run_id
        registry.approve_run(s, run_id)

    df2 = df.copy()
    df2.loc[0, "price_reported"] = 5.0
    with db.session_scope() as s:
        corrected = registry.correct_run(s, run_id, df2, cfg, "test", reason="transcription error")
        assert corrected.vintage == 2
        assert corrected.supersedes_run_id == run_id
        assert corrected.correction_reason == "transcription error"

    with db.session_scope() as s:
        original = s.query(registry.IndexRunORM).filter_by(run_id=run_id).one()
        assert original.approved is True
        assert original.vintage == 1
        assert original.input_hash != corrected.input_hash
