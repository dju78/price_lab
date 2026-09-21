"""The quality adjustment ledger's approval record: who approved which
valuation, keyed by the input data's content hash; withdrawals leave the
row in place; and the Quality adjustment page renders for a compiler and
is unreachable for an analyst."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from pricelab.core import db, ledger
from pricelab.core.config import QualityAdjustmentEntry, get_settings
from pricelab.core.models import Role
from pricelab.core.security import create_session, create_user

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = str(REPO_ROOT / "app.py")


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", f"sqlite:///{tmp_path / 'ledger_test.db'}")
    get_settings.cache_clear()
    db.reset_db_state()
    db.init_db()
    yield
    db.reset_db_state()
    get_settings.cache_clear()


def _entry(old="A", new="B", justification="overlap month recorded by the collector"):
    return QualityAdjustmentEntry(
        old_item=old, new_item=new, category="x", period="2020-03-01", method="overlap",
        quality_ratio=1.2, parameters={"overlap_period": "2020-02-01"},
        justification=justification, approved_by="compiler1")


def test_record_and_load_round_trip_by_content_hash(fresh_db):
    with db.session_scope() as s:
        rec = ledger.record_adjustment(s, "compiler1", "hash-1", _entry())
        assert rec.id is not None
        ledger.record_adjustment(s, "compiler1", "hash-2", _entry("C", "D"))
    with db.session_scope() as s:
        entries = ledger.active_entries(s, "hash-1")
        assert len(entries) == 1
        e = entries[0]
        assert (e.old_item, e.new_item, e.method, e.quality_ratio) == ("A", "B", "overlap", 1.2)
        assert e.parameters == {"overlap_period": "2020-02-01"}
        assert e.approved_by == "compiler1" and e.approved_at
        assert ledger.active_entries(s, "hash-3") == []


def test_a_justification_is_mandatory(fresh_db):
    with db.session_scope() as s, pytest.raises(ValueError, match="justification"):
        ledger.record_adjustment(s, "compiler1", "hash-1", _entry(justification="  "))


def test_withdrawal_keeps_the_row_and_stops_applying_it(fresh_db):
    with db.session_scope() as s:
        rec = ledger.record_adjustment(s, "compiler1", "hash-1", _entry())
        record_id = rec.id
    with db.session_scope() as s:
        ledger.withdraw_adjustment(s, "admin1", record_id, "overlap price was a clearance price")
    with db.session_scope() as s:
        assert ledger.active_entries(s, "hash-1") == []
        everything = ledger.load_ledger(s, "hash-1", include_withdrawn=True)
        assert len(everything) == 1
        assert everything[0].withdrawn_by == "admin1"
        assert everything[0].withdrawal_reason == "overlap price was a clearance price"
        with pytest.raises(ValueError, match="reason"):
            ledger.withdraw_adjustment(s, "admin1", record_id, "")
        with pytest.raises(ValueError, match="no quality adjustment"):
            ledger.withdraw_adjustment(s, "admin1", 999, "x")


# ---------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------
def _token(role: Role, username: str) -> str:
    with db.session_scope() as s:
        return create_session(s, create_user(s, username, "password123", role))


def _text(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown)


def test_replacement_candidates_pairs_exits_with_entrants_in_the_same_category():
    from pages.quality_adjustment import replacement_candidates

    periods = pd.date_range("2020-01-01", periods=5, freq="MS")
    rows = []
    for i, p in enumerate(periods):
        if i <= 2:
            rows.append({"period": p, "category": "x", "item_id": "A", "price_clean": 1.0})
        if i >= 3:
            rows.append({"period": p, "category": "x", "item_id": "B", "price_clean": 1.2})
        rows.append({"period": p, "category": "x", "item_id": "C", "price_clean": 2.0})
        if i >= 4:
            rows.append({"period": p, "category": "y", "item_id": "D", "price_clean": 3.0})
    cands = replacement_candidates(pd.DataFrame(rows))
    assert len(cands) == 1
    row = cands.iloc[0]
    assert (row["old_item"], row["new_item"], row["gap_periods"]) == ("A", "B", 1)
    assert row["category"] == "x"          # D is in another category and is not paired


def test_the_app_still_boots_for_a_compiler_with_the_new_page_registered(fresh_db):
    """The page is registered in app.py's navigation; a compiler session
    must boot without exception with it there, and the page's own
    decorator -- the actual access control -- must exclude analysts and
    viewers, matching the other compile-stage pages."""
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.session_state["pricelab_session_token"] = _token(Role.COMPILER, "compiler1")
    at.run()
    assert not at.exception
    assert "Ingest and compile" in _text(at)

    import pages.quality_adjustment as page

    assert set(page.render.__pricelab_required_roles__) == {Role.ADMINISTRATOR, Role.COMPILER}


def test_the_page_renders_headlessly_without_an_analysis(fresh_db, monkeypatch):
    """Rendered directly as a compiler with no run compiled: it must say so
    rather than fail. The full valuation form needs a compiled run and
    interactive widgets, which the unit tests of the engine cover."""
    import pages.quality_adjustment as page
    from pricelab.core.security import set_current_role

    calls: list[str] = []
    monkeypatch.setattr(page.st, "info", lambda msg, *a, **k: calls.append(msg))
    monkeypatch.setattr(page.st, "markdown", lambda *a, **k: None)
    set_current_role(Role.COMPILER)
    try:
        page.render()
    finally:
        set_current_role(None)
    assert calls and "Compile a run" in calls[0]


def test_the_page_renders_the_valuation_form_and_impact_for_a_compiled_run(fresh_db, tmp_path):
    """Drive the page under AppTest with a real compiled run in session
    state: the candidates table, the default (overlap) valuation, the
    ledger and the impact section must all render without exception."""
    import numpy as np

    from pricelab import analyse
    from pricelab.core.config import IndexConfig, QualityAdjustmentConfig, RunConfig
    from pricelab.engine import quality_adjustment as qa

    periods = pd.date_range("2020-01-01", periods=6, freq="MS")
    path = np.array([10.0, 10.2, 10.4, 10.6, 10.8, 11.0])
    rows = []
    for i, p in enumerate(periods):
        if i <= 3:
            rows.append({"period": p, "category": "x", "item_id": "A", "item_name": "A",
                         "price_reported": path[i]})
        if i >= 2:
            rows.append({"period": p, "category": "x", "item_id": "B", "item_name": "B",
                         "price_reported": path[i] * 1.2})
        rows.append({"period": p, "category": "x", "item_id": "C", "item_name": "C",
                     "price_reported": 20.0 + 0.5 * i})
    df = pd.DataFrame(rows)
    adj = qa.overlap("A", "B", "x", df[df.item_id == "A"].set_index("period")["price_reported"],
                     df[df.item_id == "B"].set_index("period")["price_reported"], periods[3],
                     justification="both priced in April")
    entry = adj.to_entry("compiler1")
    with db.session_scope() as s:
        ledger.record_adjustment(s, "compiler1", "hash-page", entry)
    cfg = RunConfig(index=IndexConfig(min_matched_items=1),
                    quality_adjustment=QualityAdjustmentConfig(entries=[entry]))
    out = analyse(df, "page test", cfg)
    out.pop("charts", None)

    script = """
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.quality_adjustment as page
set_current_role(Role.COMPILER)
page.render()
"""
    at = AppTest.from_string(script, default_timeout=60)
    at.session_state["analysis"] = {**out, "label": "page test"}
    at.session_state["input_df"] = df
    at.session_state["content_hash"] = "hash-page"
    at.session_state["file_bytes"] = b"x"
    at.session_state["pricelab_username"] = "compiler1"
    at.run()
    assert not at.exception, at.exception
    text = _text(at)
    assert "Replacement candidates" in text
    assert "Value a replacement" in text
    assert "Ledger" in text and "Impact on the headline" in text
    labels = [m.label for m in at.metric]
    assert any("Effect of the adjustments" in lbl for lbl in labels)
    assert any("Quality ratio" in lbl for lbl in labels)
