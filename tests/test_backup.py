"""Backup and restore, tested the only way that means anything: register a
run, back up, destroy the database and the Parquet store, restore, and
reproduce the run from the restored registry -- the restored system must
produce the index the original did, value for value.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from pricelab import analyse, auto_configure, infer_schema, standardise
from pricelab.core import backup as bk
from pricelab.core import db
from pricelab.core.config import get_settings
from pricelab.core.registry import IndexRunORM, approve_run, register_run, reproduce
from pricelab.data import store

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "supermarket_price_collection.xlsx"


@pytest.fixture()
def deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", f"sqlite:///{tmp_path / 'live.db'}")
    monkeypatch.setenv("PRICELAB_STORE_DIR", str(tmp_path / "store"))
    get_settings.cache_clear()
    db.reset_db_state()
    db.init_db()
    yield tmp_path
    db.reset_db_state()
    get_settings.cache_clear()


def test_restore_from_backup_reproduces_an_identical_index(deployment):
    settings = get_settings()
    raw = pd.read_excel(FIXTURE, sheet_name="Price_Data")
    df = standardise(raw, infer_schema(raw))
    cfg, _ = auto_configure(df, "before backup")
    out = analyse(df, "before backup", cfg)
    before = out["result"]["indices"].copy()

    # A populated deployment: a registered, approved run; a raw layer with
    # its receipt; a cleaned layer with its transformation log.
    store.record_upload(df, kind="prices", file_name=FIXTURE.name, file_bytes=b"bytes",
                        actor="compiler1", directory=settings.store_dir)
    store.write_cleaned_layer(df, cfg, settings.store_dir)
    with db.session_scope() as s:
        run = register_run(s, df, cfg, "before backup", result=out["result"])
        approve_run(s, run.run_id)
        run_id = run.run_id
    store_files_before = sorted(p.relative_to(settings.store_dir).as_posix()
                                for p in Path(settings.store_dir).rglob("*") if p.is_file())

    manifest = bk.backup(deployment / "backups" / "b1")
    assert "database.sqlite" in manifest["files"]
    assert {f for f in manifest["files"] if f.startswith("store/")} == {
        f"store/{f}" for f in store_files_before}
    assert bk.verify(deployment / "backups" / "b1") == manifest

    # Destroy the live system.
    db.reset_db_state()
    db_path = deployment / "live.db"
    db_path.unlink()
    import shutil
    shutil.rmtree(settings.store_dir)
    assert not db_path.exists() and not Path(settings.store_dir).exists()

    # Restore, and prove the restored system is the old one.
    result = bk.restore(deployment / "backups" / "b1")
    assert result["store_files"] == len(store_files_before)
    db.reset_db_state()
    with db.session_scope() as s:
        row = s.query(IndexRunORM).filter_by(run_id=run_id).one()
        assert row.approved
        reproduced = reproduce(s, run_id, actor="auditor")
    pd.testing.assert_frame_equal(reproduced["indices"], before)
    assert reproduced["indices"]["All items"].iloc[-1] == pytest.approx(row.headline_value)

    store_files_after = sorted(p.relative_to(settings.store_dir).as_posix()
                               for p in Path(settings.store_dir).rglob("*") if p.is_file())
    assert store_files_after == store_files_before
    raw_layer = pd.read_parquet(Path(settings.store_dir) / "raw" / f"{row.input_hash}.parquet")
    assert store.content_hash_of(raw_layer) == row.input_hash
    log = store.TransformationLog.from_json(
        (Path(settings.store_dir) / "cleaned" / f"{row.input_hash}.log.json").read_text(encoding="utf-8"))
    pd.testing.assert_frame_equal(store.replay(raw_layer, log).reset_index(drop=True),
                                  reproduced["imputed"].reset_index(drop=True))


def test_restore_refuses_a_damaged_backup_and_a_live_target(deployment):
    settings = get_settings()
    (Path(settings.store_dir) / "raw").mkdir(parents=True)
    (Path(settings.store_dir) / "raw" / "x.txt").write_text("x")
    manifest = bk.backup(deployment / "b2")
    assert "store/raw/x.txt" in manifest["files"]

    with pytest.raises(bk.BackupError, match="exists"):
        bk.restore(deployment / "b2")                       # live database still there
    (deployment / "b2" / "store" / "raw" / "x.txt").write_text("tampered")
    with pytest.raises(bk.BackupError, match="damaged"):
        bk.restore(deployment / "b2", overwrite=True)
    with pytest.raises(bk.BackupError, match="already holds"):
        bk.backup(deployment / "b2")


def test_backup_names_a_non_sqlite_database_as_out_of_scope(deployment):
    with pytest.raises(bk.BackupError, match="pg_dump"):
        bk.backup(deployment / "b3", database_url="postgresql://x/y")


def test_the_scripts_run(deployment, capsys):
    import subprocess
    import sys

    settings = get_settings()
    env = {"PRICELAB_DATABASE_URL": settings.database_url, "PRICELAB_STORE_DIR": settings.store_dir}
    import os
    full_env = {**os.environ, **env}
    out = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "backup.py"), "--to",
                          str(deployment / "scripted")], capture_output=True, text=True, env=full_env,
                         check=True)
    made = json.loads(out.stdout)["backup"]
    assert Path(made, "manifest.json").exists()
    db.reset_db_state()      # the application is stopped before a restore; release the file
    out = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "restore.py"), made,
                          "--overwrite"], capture_output=True, text=True, env=full_env, check=True)
    assert json.loads(out.stdout)["database"].endswith("live.db")
