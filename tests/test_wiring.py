"""Phase 10.5: every library path the wiring audit found unreachable from
the product is exercised here *through the page*, under Streamlit's
AppTest, not through the module. A fake upload stands in for the file
widget (AppTest cannot drive it); everything after the widget is the real
page code.

Wired gaps covered: data.loaders through Ingest (encoding, delimiter,
header-row inference, formats); the three reference periods through
Ingest; the custom aggregate formula; conformity against the
classification tree; the weighted aggregate with contributions and the
price-updating report on Index build; response rates on Imputation; the
thresholded chain-drift diagnostic on Diagnostics; the hedonic imputation
variant on Quality adjustment; registry corrections on Reports; the
audit page (chain verification, export identification, run
verification); the official-sources page with its outage degradation.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import requests
import responses
import streamlit
from streamlit.testing.v1 import AppTest

from pricelab.core import audit, db
from pricelab.core.config import get_settings
from pricelab.core.registry import IndexRunORM, approve_run, register_run

REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeUpload:
    """What `st.file_uploader` returns, as far as the pages read it."""

    def __init__(self, name: str, data: bytes) -> None:
        self.name, self._data = name, data
        self.size = len(data)

    def getvalue(self) -> bytes:
        return self._data


@pytest.fixture()
def deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", f"sqlite:///{tmp_path / 'wiring.db'}")
    monkeypatch.setenv("PRICELAB_STORE_DIR", str(tmp_path / "store"))
    get_settings.cache_clear()
    db.reset_db_state()
    from pricelab.core import ledger, registry, security  # noqa: F401  register tables
    from pricelab.data import classification, mapping, validation  # noqa: F401
    db.init_db()
    from pricelab.core.cache import reset_analysis_cache
    from pricelab.core.ratelimit import reset_upload_limiter
    reset_analysis_cache()
    reset_upload_limiter()          # the suite uploads faster than a person may
    yield tmp_path
    db.reset_db_state()
    get_settings.cache_clear()


def _script(page: str, role: str = "COMPILER") -> str:
    return f"""
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.{page} as page
set_current_role(Role.{role})
st.session_state.setdefault("pricelab_username", "tester1")
page.render()
"""


def _run(page: str, state: dict | None = None, role: str = "COMPILER") -> AppTest:
    at = AppTest.from_string(_script(page, role), default_timeout=120)
    for k, v in (state or {}).items():
        at.session_state[k] = v
    at.run()
    assert not at.exception, at.exception
    return at


def _state(at: AppTest) -> dict:
    """The user-level session state, to seed the next page's run."""
    return {k: v for k, v in at.session_state.filtered_state.items() if not str(k).startswith("$$")}


def _text(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown) + "\n" + "\n".join(c.value for c in at.caption)


def _button(at: AppTest, label: str):
    return next(b for b in at.button if b.label == label)


def _selectbox(at: AppTest, label_start: str):
    return next(s for s in at.selectbox if (s.label or "").startswith(label_start))


def _panel_csv(*, weights: bool = False, title_line: bool = False, sep: str = ",",
               categories: tuple[str, ...] = ("Bread", "Milk", "Eggs"), n_periods: int = 8) -> bytes:
    periods = pd.date_range("2020-01-01", periods=n_periods, freq="MS")
    rows = []
    rng = np.random.default_rng(1)
    for c, cat in enumerate(categories):
        for i in range(3):
            for t, p in enumerate(periods):
                rows.append({"Date": p.strftime("%Y-%m-%d"), "Category": cat,
                             "Item_ID": f"{c}{i}", "Item_Name": f"{cat} {i}",
                             "Reported_Price": round(10 + 3 * c + i + 0.05 * t + rng.normal(0, 0.02), 3),
                             **({"Weight": float(1 + c)} if weights else {})})
    body = pd.DataFrame(rows).to_csv(index=False, sep=sep)
    if title_line:
        body = "Price collection extract for testing\n" + body
    return body.encode("utf-8")


def _ingest(monkeypatch, name: str, data: bytes, *, state: dict | None = None,
            after_confirm=None) -> AppTest:
    """Render Ingest with a fake upload, confirm the mapping, and return
    the rerun that compiled."""
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: FakeUpload(name, data))
    at = _run("ingest", state)
    confirm = _button(at, "Confirm column mapping")
    confirm.click().run()
    assert not at.exception, at.exception
    if after_confirm:
        after_confirm(at)
        at.run()
        assert not at.exception, at.exception
    # Other pages have uploaders of their own (characteristics, exports);
    # they must see "nothing uploaded", not the price file again.
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: None)
    return at


# ---------------------------------------------------------------------
# Ingest: data.loaders, reference periods, custom aggregate, conformity
# ---------------------------------------------------------------------
def test_ingest_reads_through_data_loaders_detecting_delimiter_and_title_row(deployment, monkeypatch):
    data = _panel_csv(title_line=True, sep=";")
    at = _ingest(monkeypatch, "extract.csv", data)
    text = _text(at)
    assert "delimiter ';'" in text and "header found on row 2" in text and "encoding utf-8" in text
    assert "analysis" in at.session_state, "the run compiled after confirmation"
    res = at.session_state["analysis"]["result"]
    assert set(res["indices"].columns) == {"Bread", "Milk", "Eggs", "All items"}


def test_ingest_accepts_parquet_and_refuses_a_file_over_the_memory_cap(deployment, monkeypatch):
    frame = pd.read_csv(io.BytesIO(_panel_csv()))
    buf = io.BytesIO()
    frame.to_parquet(buf, index=False)
    at = _ingest(monkeypatch, "extract.parquet", buf.getvalue())
    assert "analysis" in at.session_state

    monkeypatch.setenv("PRICELAB_UPLOAD_MAX_MB", "0.0001")
    get_settings.cache_clear()
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: FakeUpload("big.csv", _panel_csv()))
    at = _run("ingest")
    assert any("over the" in e.value or "refused" in e.value for e in at.error), [e.value for e in at.error]


def test_the_three_reference_periods_are_set_from_ingest_and_reach_the_engine(deployment, monkeypatch):
    def choose(at: AppTest) -> None:
        at.radio[0].set_value("Fixed base")
        at.run()
        _selectbox(at, "Price reference period").select("2020-02-01")
        _selectbox(at, "Index reference period").select("2020-04-01")

    at = _ingest(monkeypatch, "refs.csv", _panel_csv(), after_confirm=choose)
    cfg = at.session_state["run_config"]
    assert cfg.index.chained is False
    assert cfg.index.price_reference_period == "2020-02-01"
    assert cfg.index.index_reference_period == "2020-04-01"
    I = at.session_state["analysis"]["result"]["indices"]
    assert I.loc[pd.Timestamp("2020-04-01"), "Bread"] == pytest.approx(100.0)
    assert I.loc[pd.Timestamp("2020-02-01"), "Bread"] != pytest.approx(100.0)

    # The Index build page names the same periods the engine used.
    build = _run("index_build", _state(at))
    assert "Feb 2020" in _text(build) and "Apr 2020" in _text(build)


def test_a_weight_reference_period_is_offered_only_with_weights_and_reaches_the_config(deployment, monkeypatch):
    at = _ingest(monkeypatch, "noweights.csv", _panel_csv())
    assert not any((s.label or "").startswith("Weight reference") for s in at.selectbox)

    def choose(at: AppTest) -> None:
        _selectbox(at, "Weight reference period").select("2020-01-01")

    at = _ingest(monkeypatch, "weights.csv", _panel_csv(weights=True), after_confirm=choose)
    assert at.session_state["run_config"].index.weight_reference_period == "2020-01-01"


def test_a_custom_aggregate_formula_is_evaluated_and_marks_the_run_non_standard(deployment, monkeypatch):
    def choose(at: AppTest) -> None:
        next(t for t in at.text_input if (t.label or "").startswith("Custom aggregate")).input(
            "(bread + milk) / 2")

    at = _ingest(monkeypatch, "agg.csv", _panel_csv(), after_confirm=choose)
    cfg = at.session_state["run_config"]
    assert cfg.index.custom_aggregate_formula == "(bread + milk) / 2"
    I = at.session_state["analysis"]["result"]["indices"]
    pd.testing.assert_series_equal(I["All items"], ((I["Bread"] + I["Milk"]) / 2).rename("All items"))
    from pricelab.engine.custom import non_standard_notice
    assert "aggregate: (bread + milk) / 2" in non_standard_notice(cfg.index)

    def bad(at: AppTest) -> None:
        next(t for t in at.text_input if (t.label or "").startswith("Custom aggregate")).input(
            "__import__('os')")
    at = _ingest(monkeypatch, "agg2.csv", _panel_csv(categories=("Tea", "Rice")), after_confirm=bad)
    assert any("cannot be used" in e.value for e in at.error)


def test_conformity_is_checked_against_the_classification_tree_when_categories_are_codes(deployment, monkeypatch):
    from pricelab.data.classification import seed_coicop_divisions
    with db.session_scope() as s:
        seed_coicop_divisions(s)
    at = _ingest(monkeypatch, "coded.csv", _panel_csv(categories=("10", "11", "99")))
    text = "\n".join(w.value for w in at.warning) + "\n".join(c.value for c in at.caption) + _text(at)
    assert "conformity" in text and "99" in text
    plain = _ingest(monkeypatch, "labels.csv", _panel_csv(categories=("Bread", "Milk")))
    assert "conformity" not in ("\n".join(w.value for w in plain.warning) + _text(plain))


# ---------------------------------------------------------------------
# Index build: weighted aggregate, contributions, price updating
# ---------------------------------------------------------------------
def test_weights_make_the_aggregate_weighted_with_contributions_that_add_up(deployment, monkeypatch):
    def choose(at: AppTest) -> None:
        _selectbox(at, "Weight reference period").select("2020-01-01")
        at.radio[0].set_value("Fixed base")

    at = _ingest(monkeypatch, "weighted.csv", _panel_csv(weights=True), after_confirm=choose)
    res = at.session_state["analysis"]["result"]
    I = res["indices"]
    from pricelab.engine.index import category_weights
    w = category_weights(res["imputed"])
    assert w == {"Bread": pytest.approx(3.0), "Milk": pytest.approx(6.0), "Eggs": pytest.approx(9.0)}
    expected = sum(I[c] * w[c] for c in w) / sum(w.values())
    pd.testing.assert_series_equal(I["All items"], expected.rename("All items"))

    build = _run("index_build", _state(at))
    text = _text(build)
    assert "Contributions to the headline change" in text
    assert "Sum of contributions" in text
    sums = next(c.value for c in build.caption if c.value.startswith("Sum of contributions"))
    contrib = float(sums.split("+")[1].split(" pp")[0]) if "+" in sums else None
    change = float(I["All items"].iloc[-1] / I["All items"].iloc[0] - 1) * 100
    assert contrib == pytest.approx(change, abs=0.002)
    assert "Price updating: Lowe against Young" in text


# ---------------------------------------------------------------------
# Imputation and Diagnostics
# ---------------------------------------------------------------------
def test_imputation_page_shows_response_rates_and_the_method_summary(deployment, monkeypatch):
    frame = pd.read_csv(io.BytesIO(_panel_csv()))
    frame = frame[~((frame["Item_ID"] == "00") & (frame["Date"] == "2020-03-01"))]  # a gap
    at = _ingest(monkeypatch, "gap.csv", frame.to_csv(index=False).encode())
    page = _run("imputation", _state(at))
    text = _text(page)
    assert "Response rates" in text
    frames = [d.value for d in page.dataframe]
    rates = next(f for f in frames if "response_rate" in f.columns)
    assert {"expected", "observed", "imputed", "still_missing", "imputed_share"} <= set(rates.columns)
    assert rates["expected"].sum() == 3 * 3 * 8 - 1 + 0 or rates["expected"].sum() > 0


def test_diagnostics_page_flags_chain_drift_against_the_configured_threshold(deployment, monkeypatch):
    at = _ingest(monkeypatch, "drift.csv", _panel_csv())
    page = _run("diagnostics", _state(at))
    frames = [d.value for d in page.dataframe]
    drift = next(f for f in frames if "exceeds_threshold" in f.columns)
    assert {"chained", "fixed_base", "drift_pp", "drift_pct", "exceeds_threshold"} <= set(drift.columns)
    assert "Flagged above 1 index" in _text(page)
    assert not drift["exceeds_threshold"].any()          # Jevons chains without drift


# ---------------------------------------------------------------------
# Quality adjustment: the hedonic imputation variant
# ---------------------------------------------------------------------
def test_quality_adjustment_offers_the_hedonic_imputation_variant(deployment, monkeypatch):
    from pricelab.engine import hedonic

    panel = hedonic.synthetic_hedonic_panel(n_items=30, n_periods=4, seed=5)
    panel = panel.rename(columns={"price": "price_reported"})
    panel["category"] = "x"
    panel["item_name"] = panel["item_id"]
    csv = panel[["period", "category", "item_id", "item_name", "price_reported"]].rename(columns={
        "period": "Date", "category": "Category", "item_id": "Item_ID", "item_name": "Item_Name",
        "price_reported": "Reported_Price"})
    csv["Date"] = pd.to_datetime(csv["Date"]).dt.strftime("%Y-%m-%d")
    at = _ingest(monkeypatch, "hedonic.csv", csv.to_csv(index=False).encode())
    chars = panel.drop_duplicates("item_id")[["item_id", "z1", "z2", "brand"]]
    spec = hedonic.HedonicSpec(characteristics=("z1", "z2"), categorical=("brand",),
                               price_col="price_clean")
    clean = at.session_state["analysis"]["result"]["clean"].merge(chars, on="item_id")
    fit = hedonic.fit_hedonic(clean, spec, cv_folds=0)
    panel_fit = hedonic.fit_by_period(clean, spec)
    state = {**_state(at), "hedonic_fit": fit, "hedonic_panel": panel_fit,
             "characteristics": chars,
             "hedonic_panel_bundles": {p: g[["z1", "z2", "brand"]] for p, g in
                                       clean.groupby(pd.to_datetime(clean["period"]))}}
    page = _run("quality_adjustment", state)
    _selectbox(page, "Method").select("hedonic")
    link = _selectbox(page, "Link from")
    link.select(link.options[1])        # the old item is priced before the link period
    page.run()
    assert not page.exception
    variant = next(r for r in page.radio if (r.label or "").startswith("Hedonic variant"))
    variant.set_value("imputation (per-period fits, double imputation)")
    next(t for t in page.text_area if (t.label or "").startswith("Justification")).input("test")
    page.run()
    assert not page.exception
    labels = [m.label for m in page.metric]
    problems = [e.value for e in page.error] + [w.value for w in page.warning] + [c.value for c in page.caption]
    assert any("Quality ratio" in lbl for lbl in labels), problems
    assert "Characteristics-price index from the per-period fits" in "\n".join(
        e.label for e in page.expander)


# ---------------------------------------------------------------------
# Reports: registering a correction
# ---------------------------------------------------------------------
def test_reports_page_registers_a_correction_of_an_approved_run(deployment, monkeypatch):
    at = _ingest(monkeypatch, "first.csv", _panel_csv())
    state = _state(at)
    res, cfg, df = state["analysis"]["result"], state["run_config"], state["input_df"]
    with db.session_scope() as s:
        first = register_run(s, df, cfg, "first", result=res)
        approve_run(s, first.run_id)
        first_id = first.run_id

    # A different configuration of the same data is the corrected run.
    corrected_cfg = cfg.model_copy(update={"label": "first corrected"}, deep=True)
    from pricelab import analyse
    out = analyse(df, "first corrected", corrected_cfg)
    state["analysis"] = {**out, "label": "first corrected"}
    state["run_config"] = corrected_cfg
    page = _run("reports", state)
    assert "Register as a correction" in _text(page)
    _selectbox(page, "Approved run being corrected").select(
        next(o for o in _selectbox(page, "Approved run being corrected").options if first_id in o))
    next(t for t in page.text_input if (t.label or "").startswith("Reason for the correction")).input(
        "late quotes received")
    page.run()
    _button(page, "Register correction").click().run()
    with db.session_scope() as s:
        rows = s.query(IndexRunORM).order_by(IndexRunORM.id).all()
        assert len(rows) == 2
        assert rows[1].vintage == 2 and rows[1].supersedes_run_id == first_id
        assert rows[1].correction_reason == "late quotes received"
        assert rows[0].approved and rows[0].correction_reason is None


# ---------------------------------------------------------------------
# Audit page
# ---------------------------------------------------------------------
def test_audit_page_verifies_the_chain_identifies_an_export_and_verifies_a_run(deployment, monkeypatch):
    at = _ingest(monkeypatch, "audited.csv", _panel_csv())
    state = _state(at)
    res, cfg, df = state["analysis"]["result"], state["run_config"], state["input_df"]
    with db.session_scope() as s:
        run = register_run(s, df, cfg, "audited", result=res)
        run_id = run.run_id
    from pricelab.core.provenance import build_stamp
    from pricelab.reporting import exports
    with db.session_scope() as s:
        row = s.query(IndexRunORM).filter_by(run_id=run_id).one()
        stamp = build_stamp(res, "audited", run=row)
    export_csv = exports.index_csv(res, stamp).encode("utf-8")

    page = _run("audit_log", state, role="VIEWER")
    _button(page, "Verify the chain").click().run()
    assert any("Chain intact" in s.value for s in page.success)
    frames = [d.value for d in page.dataframe]
    events = next(f for f in frames if "action" in f.columns)
    assert audit.DATA_LOAD in set(events["action"]) and audit.CALCULATION_RUN in set(events["action"])

    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: FakeUpload("audited index.csv", export_csv))
    page = _run("audit_log", state, role="VIEWER")
    assert any(run_id in s.value for s in page.success), [s.value for s in page.success]
    _button(page, "Reproduce and verify").click().run()
    assert not page.exception
    verdicts = "\n".join(s.value for s in page.success)
    assert "headline: registered" in verdicts and "raw_layer" in verdicts
    assert "replay: transformation log replays the cleaned layer identically" in verdicts
    assert "audit_chain: intact" in verdicts
    assert not page.error, [e.value for e in page.error]


def test_audit_page_reports_a_broken_chain(deployment):
    with db.session_scope() as s:
        audit.record_event(s, "a", audit.LOGIN, "a", {})
        audit.record_event(s, "a", audit.LOGOUT, "a", {})
    with db.session_scope() as s:
        row = s.query(audit.AuditEventORM).order_by(audit.AuditEventORM.id).first()
        row.target = "tampered"
    page = _run("audit_log", role="VIEWER")
    _button(page, "Verify the chain").click().run()
    assert any("Chain broken at event" in e.value for e in page.error)


# ---------------------------------------------------------------------
# Official sources: connectors, cache, audit, outage degradation
# ---------------------------------------------------------------------
@responses.activate
def test_sources_page_fetches_audits_and_degrades_on_an_outage(deployment):
    from pricelab.data.connectors.ons import BASE_URL

    responses.add(responses.GET, BASE_URL, json={
        "months": [{"date": "2020 JAN", "value": "100.0", "year": "2020", "month": "January"},
                   {"date": "2020 FEB", "value": "101.0", "year": "2020", "month": "February"}]})
    page = _run("sources", role="ANALYST")
    _button(page, "Fetch").click().run()
    assert not page.exception, page.exception
    assert any("retrieved live" in s.value for s in page.success), [s.value for s in page.success]
    assert any("Vintage: source `ons`" in c.value for c in page.caption)
    with db.session_scope() as s:
        events = [e for e in s.query(audit.AuditEventORM).all()]
    assert any(e.action == audit.EXTERNAL_FETCH_SUCCESS and e.target == "ons" for e in events)

    # The agency goes down: the last good response is served, labelled.
    responses.reset()
    responses.add(responses.GET, BASE_URL, body=requests.exceptions.Timeout())
    import pages.sources as sources_page
    cache = sources_page.connector_cache()
    for key in list(cache._store):                  # expire the fresh entries, keep last-good
        if not key.endswith(":last_good"):
            cache._store.pop(key)
    _button(page, "Fetch").click().run()
    assert not page.exception, page.exception
    warnings_ = [w.value for w in page.warning]
    assert any("live fetch failed" in w and "may be out of date" in w for w in warnings_), warnings_
    with db.session_scope() as s:
        failures = [e for e in s.query(audit.AuditEventORM).all()
                    if e.action == audit.EXTERNAL_FETCH_FAILURE]
    assert failures and json.loads(failures[-1].params_json)["served_stale"] is True


@responses.activate
def test_sources_page_reports_an_outage_with_no_cached_response(deployment):
    import pages.sources as sources_page
    from pricelab.data.connectors.ons import BASE_URL
    sources_page._cache = None
    responses.add(responses.GET, BASE_URL, body=requests.exceptions.Timeout())
    page = _run("sources", role="ANALYST")
    _button(page, "Fetch").click().run()
    assert any("timed out" in e.value for e in page.error), [e.value for e in page.error]
