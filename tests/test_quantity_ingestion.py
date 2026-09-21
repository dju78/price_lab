"""Quantity and expenditure ingestion: the canonical schema, mapping,
validation, store and interface carry quantity, expenditure and unit; the
quantity-weighted formulae are reachable from an upload; the Phase 3
axioms that only synthetic Series could exercise now run on ingested data;
and a price-only upload is byte-for-byte what it was.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import streamlit
from dbtarget import database_url
from streamlit.testing.v1 import AppTest

from pricelab import auto_configure, infer_schema, run_pipeline, standardise
from pricelab.core import db
from pricelab.core.config import IndexConfig, RunConfig, Schema, get_settings
from pricelab.core.provenance import build_stamp
from pricelab.core.registry import _hash_dataframe, register_run
from pricelab.data import mapping, store, validation
from pricelab.data.upload import validate
from pricelab.engine import bilateral
from pricelab.engine.index import QUANTITY_FORMULAE, formula_availability, quantity_series

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "quantity_panel.csv"
PRICES_ONLY = REPO_ROOT / "supermarket_price_collection.xlsx"


def _quantity_panel() -> pd.DataFrame:
    raw = pd.read_csv(FIXTURE)
    return standardise(raw, infer_schema(raw))


# ---------------------------------------------------------------------
# Schema, standardisation, inference, mapping
# ---------------------------------------------------------------------
def test_the_canonical_schema_carries_quantity_expenditure_and_unit_optionally():
    raw = pd.read_csv(FIXTURE)
    schema = infer_schema(raw)
    assert (schema.quantity, schema.expenditure, schema.unit) == ("Quantity", "Expenditure", "Unit")
    assert schema.price == "Reported_Price" and schema.weight is None
    df = standardise(raw, schema)
    assert {"quantity", "expenditure", "unit"} <= set(df.columns)
    assert pd.api.types.is_numeric_dtype(df["quantity"]) and pd.api.types.is_numeric_dtype(df["expenditure"])
    assert pd.api.types.is_string_dtype(df["unit"]) and set(df["unit"]) == {"pack"}
    # optional: a price-only schema still standardises to exactly the old columns
    plain = standardise(raw.drop(columns=["Quantity", "Expenditure", "Unit"]),
                        infer_schema(raw.drop(columns=["Quantity", "Expenditure", "Unit"])))
    assert list(plain.columns) == ["period", "category", "item_id", "item_name", "price_reported"]
    assert Schema().quantity is None and Schema().expenditure is None and Schema().unit is None


def test_inference_does_not_mistake_expenditure_or_quantity_for_price_or_weight():
    raw = pd.read_csv(FIXTURE).rename(columns={"Reported_Price": "Unit price", "Expenditure": "Sales value",
                                                 "Quantity": "Units sold"})
    schema = infer_schema(raw)
    assert schema.price == "Unit price"
    assert schema.expenditure == "Sales value" and schema.quantity == "Units sold"
    assert schema.weight is None                     # expenditure is no longer read as a weight
    suggestions = mapping.suggest_column_mapping(raw)
    assert set(mapping.CANONICAL_FIELDS) >= {"quantity", "expenditure", "unit"}
    assert suggestions["quantity"].column == "Units sold"
    assert suggestions["expenditure"].confidence >= 0.8


def test_the_price_only_fixture_infers_no_quantity_fields_and_is_unchanged():
    raw = pd.read_excel(PRICES_ONLY, sheet_name="Price_Data")
    schema = infer_schema(raw)
    assert (schema.quantity, schema.expenditure, schema.unit) == (None, None, None)
    df = standardise(raw, schema)
    assert not {"quantity", "expenditure", "unit"} & set(df.columns)
    cfg, _ = auto_configure(df, "hard gate")
    res = run_pipeline(df, cfg)
    expected = pd.read_parquet(REPO_ROOT / "tests" / "fixtures" / "phase3_baseline_index.parquet")
    expected.index = pd.to_datetime(expected.index)
    pd.testing.assert_frame_equal(res["indices"], expected, check_names=False)
    assert res["expenditure_check"].empty
    assert quantity_series(df) is None
    unavailable = {k: v for k, v in formula_availability(df).items() if v}
    assert set(unavailable) == set(QUANTITY_FORMULAE) - {"laspeyres"}
    assert all("needs a quantity column" in v for v in unavailable.values())
    assert "homogeneity" in unavailable["unit_value"]


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------
def test_expenditure_inconsistent_with_price_times_quantity_is_reported_not_absorbed():
    df = _quantity_panel()
    bad_rows = df.index[[3, 40, 77]]
    df.loc[bad_rows, "expenditure"] = df.loc[bad_rows, "expenditure"] * 1.25
    assessment = validation.assess(df, reference_date=pd.Timestamp("2024-01-15"))
    finding = next(f for f in assessment.findings
                   if f.dimension == "consistency" and "expenditure differs" in f.message)
    assert finding.severity == validation.Severity.HIGH and finding.count == 3
    assert "neither figure has been preferred" in finding.message.lower()
    assert not assessment.blocking
    listed = validation.expenditure_inconsistencies(df, 0.01)
    assert len(listed) == 3 and np.allclose(listed["relative_gap"], 0.2)   # (1.25 - 1) / 1.25
    # the quantity column is used as given: the index does not change
    clean = _quantity_panel()
    same = run_pipeline(clean, RunConfig(index=IndexConfig(formula="fisher", chained=False)))
    changed = run_pipeline(df, RunConfig(index=IndexConfig(formula="fisher", chained=False)))
    pd.testing.assert_frame_equal(same["indices"], changed["indices"])
    assert len(changed["expenditure_check"]) == 3        # and the run carries the list
    # within tolerance is not a finding
    close = _quantity_panel()
    close["expenditure"] *= 1.005
    assert not any("expenditure differs" in f.message
                   for f in validation.assess(close, reference_date=pd.Timestamp("2024-01-15")).findings)


def test_negative_quantities_are_structural_errors_and_expenditure_only_is_derived():
    df = _quantity_panel()
    df.loc[df.index[0], "quantity"] = -1.0
    report = validate(df)
    assert not report.passed and any("negative quantity" in e for e in report.errors)

    only_e = _quantity_panel().drop(columns=["quantity"])
    q = quantity_series(only_e, "price_reported")
    assert q is not None and q.name == "quantity_derived"
    np.testing.assert_allclose(q.to_numpy(), (only_e["expenditure"] / only_e["price_reported"]).to_numpy())
    notes = validation.assess(only_e, reference_date=pd.Timestamp("2024-01-15")).findings
    assert any("derived as expenditure / price" in f.message for f in notes)
    res = run_pipeline(only_e, RunConfig(index=IndexConfig(formula="fisher", chained=False)))
    with_q = run_pipeline(_quantity_panel(), RunConfig(index=IndexConfig(formula="fisher", chained=False)))
    pd.testing.assert_frame_equal(res["indices"], with_q["indices"], check_exact=False, atol=1e-6)


# ---------------------------------------------------------------------
# The formulae, on ingested data
# ---------------------------------------------------------------------
def _final_levels(df: pd.DataFrame, *formulae: str, chained: bool = False) -> dict[str, pd.Series]:
    out = {}
    for f in formulae:
        res = run_pipeline(df, RunConfig(index=IndexConfig(formula=f, chained=chained, min_matched_items=2)))
        out[f] = res["indices"]["All items"]
    return out


def test_a_quantity_upload_produces_laspeyres_paasche_and_fisher_in_the_expected_order():
    """Appendix 2 test 2, on ingested data: with quantities responding
    inversely to prices, Laspeyres exceeds Fisher exceeds Paasche in every
    period after the base, and the substitution bias is the gap."""
    df = _quantity_panel()
    levels = _final_levels(df, "laspeyres", "paasche", "fisher")
    after_base = levels["laspeyres"].index[1:]
    assert (levels["laspeyres"][after_base] > levels["fisher"][after_base]).all()
    assert (levels["fisher"][after_base] > levels["paasche"][after_base]).all()
    bias_pp = levels["laspeyres"].iloc[-1] - levels["paasche"].iloc[-1]
    assert bias_pp > 0.5
    assert np.isclose(levels["fisher"].iloc[-1],
                      np.sqrt(levels["laspeyres"].iloc[-1] * levels["paasche"].iloc[-1]), atol=0.05)


def test_factor_reversal_holds_on_the_ingested_panel():
    """Fisher price x Fisher quantity = value ratio, computed per category
    from the ingested prices and quantities themselves."""
    df = _quantity_panel()
    for cat, d in df.groupby("category"):
        pivot_p = d.pivot(index="period", columns="item_id", values="price_reported")
        pivot_q = d.pivot(index="period", columns="item_id", values="quantity")
        p0, pt = pivot_p.iloc[0], pivot_p.iloc[-1]
        q0, qt = pivot_q.iloc[0], pivot_q.iloc[-1]
        price = bilateral.fisher(p0, pt, q0, qt).value
        quantity = bilateral.fisher_quantity(p0, pt, q0, qt).value
        assert price * quantity == pytest.approx(bilateral.value_ratio(p0, pt, q0, qt), rel=1e-9), cat


def test_every_quantity_formula_compiles_and_the_superlatives_agree():
    df = _quantity_panel()
    names = [n for n in QUANTITY_FORMULAE if n != "unit_value"]
    levels = _final_levels(df, *names)
    finals = {n: float(s.iloc[-1]) for n, s in levels.items()}
    assert all(np.isfinite(v) for v in finals.values())
    assert abs(finals["fisher"] - finals["tornqvist"]) < 0.5
    assert abs(finals["fisher"] - finals["walsh"]) < 0.5
    assert finals["geometric_laspeyres"] < finals["laspeyres"]
    assert finals["geometric_paasche"] > finals["paasche"]
    chained = _final_levels(df, "fisher", chained=True)["fisher"]
    assert np.isfinite(chained.iloc[-1])


def test_unit_value_needs_the_homogeneity_assertion_and_then_compiles():
    df = _quantity_panel()
    with pytest.raises(ValueError, match="homogeneity_justification"):
        IndexConfig(formula="unit_value")
    cfg = RunConfig(index=IndexConfig(formula="unit_value", chained=False,
                                      homogeneity_justification="single-pack varieties of one product"))
    res = run_pipeline(df, cfg)
    assert np.isfinite(res["indices"]["All items"].iloc[-1])
    assert "single-pack" in cfg.to_json()          # the assertion travels with the run
    with pytest.raises(ValueError, match="needs quantities"):
        run_pipeline(standardise(pd.read_excel(PRICES_ONLY, sheet_name="Price_Data"),
                                 infer_schema(pd.read_excel(PRICES_ONLY, sheet_name="Price_Data"))),
                     RunConfig(index=IndexConfig(formula="fisher")))


# ---------------------------------------------------------------------
# Provenance: raw layer, transformation log, vintage stamp, registry hash
# ---------------------------------------------------------------------
@pytest.fixture()
def deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "qty.db"))
    monkeypatch.setenv("PRICELAB_STORE_DIR", str(tmp_path / "store"))
    get_settings.cache_clear()
    db.reset_db_state()
    from pricelab.core import ledger, registry, security  # noqa: F401
    from pricelab.data import classification  # noqa: F401
    db.init_db()
    from pricelab.core.cache import reset_analysis_cache
    from pricelab.core.ratelimit import reset_upload_limiter
    reset_analysis_cache()
    reset_upload_limiter()
    yield tmp_path
    db.reset_db_state()
    get_settings.cache_clear()


def test_the_new_fields_are_in_the_raw_layer_log_stamp_and_registry_hash(deployment):
    settings = get_settings()
    raw = pd.read_csv(FIXTURE)
    schema = infer_schema(raw)
    df = standardise(raw, schema)
    vintage = store.record_upload(df, kind="prices", file_name=FIXTURE.name,
                                  file_bytes=FIXTURE.read_bytes(), actor="c", directory=settings.store_dir)
    raw_layer = pd.read_parquet(vintage.raw_path)
    assert {"quantity", "expenditure", "unit"} <= set(raw_layer.columns)
    pd.testing.assert_frame_equal(raw_layer, df)

    cfg = RunConfig(schema=schema, index=IndexConfig(formula="fisher", chained=False))
    _path, log = store.write_cleaned_layer(df, cfg, settings.store_dir)
    logged = json.loads(log.to_json())["config"]["schema"]
    assert logged["quantity"] == "Quantity" and logged["expenditure"] == "Expenditure" and logged["unit"] == "Unit"
    replayed = store.replay(raw_layer, store.TransformationLog.from_json(log.to_json()))
    assert {"quantity", "expenditure"} <= set(replayed.columns)

    res = run_pipeline(df, cfg)
    with db.session_scope() as s:
        run = register_run(s, df, cfg, "qty", result=res, data_source=vintage.source)
        stamp = build_stamp(res, "qty", run=run)
        input_hash = run.input_hash
    assert stamp.parameters["schema"]["quantity"] == "Quantity"
    assert stamp.parameters["index"]["formula"] == "fisher"
    assert stamp.data_vintage == vintage.content_hash == input_hash

    # The same prices without the quantity columns are a different input,
    # hence a different data vintage and a different run.
    prices_only = df.drop(columns=["quantity", "expenditure", "unit"])
    assert _hash_dataframe(prices_only) != input_hash
    assert store.content_hash_of(prices_only) != vintage.content_hash


# ---------------------------------------------------------------------
# The page: upload with quantities, choose Fisher, obtain a result
# ---------------------------------------------------------------------
class FakeUpload:
    def __init__(self, name: str, data: bytes) -> None:
        self.name, self._data, self.size = name, data, len(data)

    def getvalue(self) -> bytes:
        return self._data


def _run_ingest(monkeypatch, name: str, data: bytes) -> AppTest:
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: FakeUpload(name, data))
    script = """
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.ingest as page
set_current_role(Role.COMPILER)
st.session_state.setdefault("pricelab_username", "tester1")
page.render()
"""
    at = AppTest.from_string(script, default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    next(b for b in at.button if b.label == "Confirm column mapping").click().run()
    assert not at.exception, at.exception
    return at


def test_a_user_can_upload_quantities_select_fisher_and_obtain_a_result(deployment, monkeypatch):
    at = _run_ingest(monkeypatch, "quantity_panel.csv", FIXTURE.read_bytes())
    # the mapping workspace offered the three new fields and inferred them
    assert "analysis" in at.session_state
    selector = next(s for s in at.selectbox if (s.label or "").startswith("Index formula"))
    assert "fisher" in selector.options                       # available, no reason attached
    assert not any("unavailable" in o for o in selector.options)
    selector.select("fisher").run()
    assert not at.exception, at.exception
    cfg = at.session_state["run_config"]
    assert cfg.index.formula == "fisher"
    assert cfg.schema_.quantity == "Quantity" and cfg.schema_.expenditure == "Expenditure"
    I = at.session_state["analysis"]["result"]["indices"]
    assert {"Bread", "Milk", "Coffee", "All items"} == set(I.columns)
    assert np.isfinite(I["All items"].iloc[-1]) and I["All items"].iloc[-1] != 100.0
    fisher_direct = run_pipeline(_quantity_panel(), cfg)["indices"]
    pd.testing.assert_frame_equal(I, fisher_direct)

    # unit value: the assertion is demanded before compiling
    selector.select("unit_value").run()
    assert any("homogeneity assertion" in w.value for w in at.warning)
    next(t for t in at.text_area if (t.label or "").startswith("Homogeneity")).input(
        "one product per category, quantities in packs").run()
    assert at.session_state["run_config"].index.formula == "unit_value"
    assert at.session_state["run_config"].index.homogeneity_justification.startswith("one product")


def test_a_price_only_upload_names_why_each_quantity_formula_is_unavailable(deployment, monkeypatch):
    prices_only = pd.read_csv(FIXTURE).drop(columns=["Quantity", "Expenditure", "Unit"])
    at = _run_ingest(monkeypatch, "prices_only.csv", prices_only.to_csv(index=False).encode())
    selector = next(s for s in at.selectbox if (s.label or "").startswith("Index formula"))
    labelled = [o for o in selector.options if "unavailable" in o]
    assert {o.split(" -- ")[0] for o in labelled} == set(QUANTITY_FORMULAE) - {"laspeyres"}
    assert all("needs a quantity column" in o for o in labelled)
    fisher_label = next(o for o in labelled if o.startswith("fisher"))
    selector.select(fisher_label).run()
    assert any("cannot be compiled on this collection" in e.value for e in at.error)
    assert at.session_state["run_config"].index.formula == "jevons"   # nothing recompiled


def test_an_inconsistent_expenditure_upload_is_reported_on_ingest_and_the_quality_page(deployment, monkeypatch):
    raw = pd.read_csv(FIXTURE)
    raw.loc[raw.index[:2], "Expenditure"] = raw.loc[raw.index[:2], "Expenditure"] * 2
    at = _run_ingest(monkeypatch, "inconsistent.csv", raw.to_csv(index=False).encode())
    warnings_ = "\n".join(w.value for w in at.warning)
    assert "expenditure differs from price x quantity" in warnings_
    assert len(at.session_state["analysis"]["result"]["expenditure_check"]) == 2
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: None)
    script = """
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.quality as page
set_current_role(Role.COMPILER)
page.render()
"""
    page = AppTest.from_string(script, default_timeout=60)
    for k, v in at.session_state.filtered_state.items():
        if not str(k).startswith("$$"):
            page.session_state[k] = v
    page.run()
    assert not page.exception, page.exception
    assert "Expenditure inconsistent with price x quantity" in "\n".join(m.value for m in page.markdown)
    frames = [d.value for d in page.dataframe]
    assert any("implied_expenditure" in f.columns and len(f) == 2 for f in frames)


def test_read_price_data_reads_quantities_from_a_path():
    from pricelab.data.upload import read_price_data
    df = read_price_data(str(FIXTURE))
    assert {"quantity", "expenditure", "unit"} <= set(df.columns)
    assert io is not None
