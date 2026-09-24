"""Provenance carries the commit: the full hash of the code a run was made
with, flagged when the working tree differed from it, and a stated
"unknown" when it cannot be known."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest
from dbtarget import database_url

from pricelab.core import db, registry
from pricelab.core.config import RunConfig, get_settings
from pricelab.core.provenance import build_stamp

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
REPO_ROOT = Path(__file__).resolve().parents[1]


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.fixture()
def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "test")
    (root / "module.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "module.py")
    _git(root, "commit", "-q", "-m", "one")
    return root


@needs_git
def test_a_clean_checkout_is_its_one_full_commit(checkout):
    state = registry.code_state(checkout)
    assert state.commit == _git(checkout, "rev-parse", "HEAD")
    assert len(state.commit) == 40
    assert state.dirty is False and state.source == "git"
    assert state.version == state.commit


@needs_git
def test_uncommitted_work_is_flagged_dirty(checkout):
    (checkout / "module.py").write_text("x = 2\n", encoding="utf-8")
    state = registry.code_state(checkout)
    assert state.dirty is True
    assert state.version == _git(checkout, "rev-parse", "HEAD") + "-dirty"
    assert "uncommitted changes" in registry.describe_code_version(state.version)

    _git(checkout, "checkout", "--", "module.py")
    (checkout / "new_module.py").write_text("y = 1\n", encoding="utf-8")
    assert registry.code_state(checkout).dirty is True, "an untracked file is uncommitted work"


@needs_git
def test_a_directory_inside_another_checkout_does_not_borrow_its_commit(checkout,
                                                                        monkeypatch):
    inner = checkout / "vendored"
    inner.mkdir()
    monkeypatch.delenv(registry.CODE_VERSION_ENV, raising=False)
    state = registry.code_state(inner)
    assert state.commit == "unknown" and state.dirty is None


def test_without_a_checkout_the_build_value_is_used_and_otherwise_unknown(tmp_path,
                                                                          monkeypatch):
    plain = tmp_path / "no_git_here"
    plain.mkdir()
    monkeypatch.setenv(registry.CODE_VERSION_ENV, "a" * 40)
    state = registry.code_state(plain)
    assert (state.commit, state.dirty, state.source) == ("a" * 40, False, "build")
    monkeypatch.setenv(registry.CODE_VERSION_ENV, "b" * 40 + "-dirty")
    assert registry.code_state(plain).dirty is True
    monkeypatch.delenv(registry.CODE_VERSION_ENV)
    state = registry.code_state(plain)
    assert (state.commit, state.source) == ("unknown", "unavailable")
    assert "cannot be identified" in registry.describe_code_version(state.version)


def test_a_registered_run_and_its_stamp_carry_the_code_state(tmp_path, monkeypatch):
    """The registry records `code_state().version` and every export's stamp
    carries it with the reading a person needs."""
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "code.db"))
    get_settings.cache_clear()
    db.reset_db_state()
    db.init_db()
    try:
        from pricelab import infer_schema, run_pipeline, standardise
        raw = pd.DataFrame({
            "Date": pd.date_range("2020-01-01", periods=3, freq="MS").repeat(2),
            "Category": ["Bread", "Milk"] * 3, "Item_ID": ["a", "b"] * 3,
            "Item_Name": ["a", "b"] * 3, "Reported_Price": [1.0, 2.0, 1.1, 2.1, 1.2, 2.2]})
        df = standardise(raw, infer_schema(raw))
        res = run_pipeline(df, RunConfig())
        expected = registry.code_state()
        with db.session_scope() as s:
            run = registry.register_run(s, df, RunConfig(), "code", result=res)
            assert run.code_version == expected.version
            stamp = build_stamp(res, "code", run=run)
        assert stamp.code_version == expected.version
        shown = dict(stamp.rows())["Code version (git commit)"]
        assert shown == registry.describe_code_version(expected.version)
        if expected.source == "git":
            assert expected.commit == _git(REPO_ROOT, "rev-parse", "HEAD")
            assert expected.dirty == bool(_git(REPO_ROOT, "status", "--porcelain"))
    finally:
        db.reset_db_state()
        get_settings.cache_clear()


def test_the_audit_check_passes_only_for_the_same_clean_commit():
    from pages.audit_log import code_version_check

    clean = "c" * 40
    assert code_version_check(clean, clean)["ok"]
    assert not code_version_check(clean, "d" * 40)["ok"]
    assert not code_version_check(clean + "-dirty", clean + "-dirty")["ok"]
    assert not code_version_check("unknown", "unknown")["ok"]
    assert "not a replay of the original" in code_version_check(clean, "d" * 40)["detail"]
