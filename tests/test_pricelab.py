"""Tests.

The index tests check axiomatic properties rather than fixed expected numbers.
A test asserting that an index equals 147.3 tells you nothing when it breaks.
A test asserting that Jevons satisfies time reversal, and that Carli does not,
encodes the reason the formula was chosen.

Run: pytest -q
"""

import numpy as np
import pandas as pd
import pytest

from pricelab import (RunConfig, QualityConfig, IndexConfig, ImputationConfig,
                      validate, run_quality, run_imputation, build_all,
                      jevons, dutot, carli, run_pipeline, build_narrative)
from pricelab.index import build_index
from pricelab.insights import trend_findings, quality_findings
from pricelab.diagnostics import unmatched_comparison


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
def panel(n_items=4, n_periods=24, start_price=2.0, monthly_growth=0.002,
          category="Test", seed=0):
    rng = np.random.default_rng(seed)
    periods = pd.date_range("2020-01-01", periods=n_periods, freq="MS")
    rows = []
    for i in range(n_items):
        p = start_price * (1 + 0.1 * i)
        for t in periods:
            rows.append({"period": t, "category": category,
                         "item_id": f"I{i}", "item_name": f"item {i}",
                         "price_reported": round(p, 4)})
            p *= (1 + monthly_growth) * (1 + rng.normal(0, 0.002))
    return pd.DataFrame(rows)


@pytest.fixture
def clean_panel():
    return panel()


# ----------------------------------------------------------------------
# Elementary formula axioms
# ----------------------------------------------------------------------
def test_identity_returns_one():
    """An unchanged price vector must give a relative of exactly 1."""
    a = pd.Series({"x": 1.0, "y": 2.0, "z": 3.0})
    for fn in (jevons, dutot, carli):
        assert fn(a, a) == pytest.approx(1.0)


def test_jevons_satisfies_time_reversal():
    """P(a,b) * P(b,a) == 1. This is the property that justifies Jevons."""
    a = pd.Series({"x": 1.0, "y": 2.0, "z": 3.0})
    b = pd.Series({"x": 1.5, "y": 1.8, "z": 4.2})
    assert jevons(a, b) * jevons(b, a) == pytest.approx(1.0)


def test_carli_fails_time_reversal_upward():
    """Carli's known upward bias, demonstrated rather than asserted in prose.
    Included so the choice against it is evidenced."""
    a = pd.Series({"x": 1.0, "y": 2.0, "z": 3.0})
    b = pd.Series({"x": 1.5, "y": 1.8, "z": 4.2})
    assert carli(a, b) * carli(b, a) > 1.0


def test_jevons_proportionality():
    """Scaling every price by k must give exactly k."""
    a = pd.Series({"x": 1.0, "y": 2.0, "z": 3.0})
    assert jevons(a, a * 1.07) == pytest.approx(1.07)


def test_dutot_is_unit_sensitive_but_jevons_is_not():
    """Re-expressing one item's quantity unit changes Dutot, not Jevons.
    This is the reason Dutot is unsafe over heterogeneous items."""
    a = pd.Series({"x": 1.0, "y": 10.0})
    b = pd.Series({"x": 1.1, "y": 10.5})
    a2, b2 = a.copy(), b.copy()
    a2["y"], b2["y"] = a["y"] * 100, b["y"] * 100     # y now priced per 100 units
    assert jevons(a, b) == pytest.approx(jevons(a2, b2))
    assert dutot(a, b) != pytest.approx(dutot(a2, b2))


def test_matching_excludes_unmatched_items():
    """An item present in only one period must not enter the comparison."""
    a = pd.Series({"x": 1.0, "y": 2.0})
    b = pd.Series({"x": 2.0, "z": 99.0})
    assert jevons(a, b) == pytest.approx(2.0)


# ----------------------------------------------------------------------
# Index construction
# ----------------------------------------------------------------------
def test_constant_prices_give_flat_index(clean_panel):
    df = clean_panel.copy()
    df["price_reported"] = 3.0
    res = run_pipeline(df)
    idx = res["indices"]["Test"]
    assert np.allclose(idx.values, 100.0)


def test_known_growth_recovered():
    """A panel growing at exactly 0.5% a month over 12 months must return
    an index of 100 * 1.005**11 at the final period."""
    df = panel(n_items=3, n_periods=12, monthly_growth=0.005, seed=1)
    df["price_reported"] = df.groupby("item_id").cumcount().map(
        lambda k: 2.0 * (1.005 ** k))
    res = run_pipeline(df)
    assert res["indices"]["Test"].iloc[-1] == pytest.approx(100 * 1.005 ** 11, rel=1e-6)


def test_base_period_reads_base_value(clean_panel):
    res = run_pipeline(clean_panel)
    assert res["indices"]["Test"].iloc[0] == pytest.approx(100.0)


def test_index_holds_level_when_no_items_match():
    """A period where nothing matches must hold the level, not produce NaN or
    zero. This is the out-of-season case."""
    df = panel(n_items=2, n_periods=6)
    gap = df["period"] == df["period"].unique()[3]
    df.loc[gap, "price_reported"] = 0
    res = run_pipeline(df)
    idx = res["indices"]["Test"]
    assert np.isfinite(idx).all()
    assert idx.iloc[3] == pytest.approx(idx.iloc[2])


def test_unmatched_base_period_raises_rather_than_all_nan():
    """A base_period that matches no real period must fail loudly. Silently
    falling back to an all-missing row would produce an all-NaN index with
    no indication the configured value, not the data, was the problem."""
    df = panel(n_items=2, n_periods=6)
    cfg = IndexConfig(chained=False, base_period="2019-06-15")
    with pytest.raises(ValueError, match="base_period"):
        run_pipeline(df, RunConfig(index=cfg))


# ----------------------------------------------------------------------
# Quality
# ----------------------------------------------------------------------
def _corrupt_and_clean(seed, position, factor):
    """Corrupt one observation by a known factor and return its cleaned row.
    Located by item and period, never by float equality on a price."""
    df = panel(n_items=3, n_periods=24, seed=seed)
    target = df.index[position]
    true_value = df.loc[target, "price_reported"]
    item, period = df.loc[target, "item_id"], df.loc[target, "period"]
    df.loc[target, "price_reported"] = true_value * factor
    clean, q = run_quality(_std(df))
    row = clean.loc[(clean["item_id"] == item) & (clean["period"] == period)].iloc[0]
    return row, true_value, q


def test_scale_error_up_is_repaired():
    row, true_value, _ = _corrupt_and_clean(seed=2, position=10, factor=100)
    assert row["flag"] == "scale_error_x100"
    assert row["price_clean"] == pytest.approx(true_value)


def test_scale_error_down_is_repaired():
    row, true_value, _ = _corrupt_and_clean(seed=3, position=15, factor=0.01)
    assert row["flag"] == "scale_error_div100"
    assert row["price_clean"] == pytest.approx(true_value)


def test_genuine_volatility_is_not_flagged():
    """A price that doubles is a price change, not a unit error. The band
    threshold must leave it alone."""
    df = panel(n_items=3, n_periods=24, seed=4)
    target = df.index[12]
    df.loc[target, "price_reported"] *= 2
    clean, _ = run_quality(_std(df))
    assert (clean["flag"] == "none").sum() == len(clean)


def test_missing_codes_become_nan_not_zero():
    df = panel(n_items=2, n_periods=6)
    df.loc[df.index[3], "price_reported"] = 0
    clean, _ = run_quality(_std(df))
    assert clean["price_clean"].min() > 0 or clean["price_clean"].isna().any()
    assert (clean["price_clean"] == 0).sum() == 0


def test_no_residual_outliers_after_repair():
    """The check that proves the repair rule was correctly specified."""
    df = panel(n_items=4, n_periods=36, seed=5)
    for i in (5, 20, 40):
        df.loc[df.index[i], "price_reported"] *= 100
    clean, q = run_quality(_std(df))
    assert len(q["residual_outliers"]) == 0


def test_seasonal_mechanism_is_classified():
    """Recurring same-month gaps across years must be labelled seasonal, not
    treated as collection failure."""
    df = panel(n_items=3, n_periods=48, seed=6)
    off_season = df["period"].dt.month.isin([11, 12, 1, 2])
    df.loc[off_season, "price_reported"] = 0
    clean, q = run_quality(_std(df))
    mech = q["missing_mechanisms"].set_index("category")["mechanism"]
    assert mech["Test"] == "seasonal"


def test_collection_gap_is_classified():
    """A one-off systemic gap must not be labelled seasonal."""
    df = panel(n_items=3, n_periods=48, seed=7)
    gap = df["period"].isin(pd.to_datetime(["2020-03-01", "2020-04-01"]))
    df.loc[gap, "price_reported"] = 0
    clean, q = run_quality(_std(df))
    mech = q["missing_mechanisms"].set_index("category")["mechanism"]
    assert mech["Test"] == "collection"


def test_seasonal_gap_under_two_years_is_not_collection():
    """Recurrence across years can't be proven with under two years on hand,
    but a systemic gap confined to a handful of calendar months still reads
    as seasonal, not collection failure: the wrong call here would trigger
    class-mean imputation and fabricate prices for an out-of-season product."""
    df = panel(n_items=3, n_periods=18, seed=8)
    off_season = df["period"].dt.month.isin([11, 12, 1])
    df.loc[off_season, "price_reported"] = 0
    clean, q = run_quality(_std(df))
    mech = q["missing_mechanisms"].set_index("category")["mechanism"]
    assert mech["Test"] == "seasonal"


def test_scale_errors_repaired_reflects_config_not_just_detection():
    """flag_summary counts detections regardless of repair_scale_errors.
    Callers displaying "faults repaired" need a count that reflects what
    actually happened to the data, not just what was found."""
    df = panel(n_items=3, n_periods=24, seed=9)
    df.loc[df.index[10], "price_reported"] *= 100
    _, q_repaired = run_quality(_std(df), QualityConfig(repair_scale_errors=True))
    assert q_repaired["scale_errors_detected"] == 1
    assert q_repaired["scale_errors_repaired"] == 1

    _, q_dropped = run_quality(_std(df), QualityConfig(repair_scale_errors=False))
    assert q_dropped["scale_errors_detected"] == 1
    assert q_dropped["scale_errors_repaired"] == 0


# ----------------------------------------------------------------------
# Imputation
# ----------------------------------------------------------------------
def test_class_mean_moves_gap_with_peers():
    """An unpriced item must move by its peers' average change, so the
    imputed relative equals the class relative."""
    periods = pd.date_range("2021-01-01", periods=2, freq="MS")
    rows = [
        {"period": periods[0], "category": "C", "item_id": "a", "item_name": "a", "price_reported": 1.00},
        {"period": periods[0], "category": "C", "item_id": "b", "item_name": "b", "price_reported": 2.00},
        {"period": periods[0], "category": "C", "item_id": "c", "item_name": "c", "price_reported": 4.00},
        {"period": periods[1], "category": "C", "item_id": "a", "item_name": "a", "price_reported": 1.10},
        {"period": periods[1], "category": "C", "item_id": "b", "item_name": "b", "price_reported": 2.20},
        {"period": periods[1], "category": "C", "item_id": "c", "item_name": "c", "price_reported": 0},
    ]
    clean, _ = run_quality(_std(pd.DataFrame(rows)))
    imp = run_imputation(clean, ImputationConfig(default_method="class_mean"))
    filled = imp.loc[(imp["item_id"] == "c") & (imp["period"] == periods[1]), "price_imputed"].iloc[0]
    assert filled == pytest.approx(4.00 * 1.10)


def test_carry_forward_holds_last_price():
    df = panel(n_items=2, n_periods=6)
    df.loc[df.index[3], "price_reported"] = 0
    clean, _ = run_quality(_std(df))
    imp = run_imputation(clean, ImputationConfig(default_method="carry_forward"))
    assert imp["price_imputed"].isna().sum() == 0


def test_class_mean_falls_back_for_single_item_category():
    """class_mean moves an item by its peers' change; a single-item category
    has no peer, so it must fall back rather than silently leave the gap
    unfilled while claiming class_mean was applied."""
    df = panel(n_items=1, n_periods=6)
    df.loc[df.index[3], "price_reported"] = 0
    clean, _ = run_quality(_std(df))
    imp = run_imputation(clean, ImputationConfig(default_method="class_mean"))
    assert imp["price_imputed"].isna().sum() == 0
    assert imp.loc[imp["price_clean"].isna(), "imputation"].eq("carry_forward").all()


# ----------------------------------------------------------------------
# Insights and diagnostics
# ----------------------------------------------------------------------
def test_single_period_data_does_not_crash_the_narrative():
    """A single-period upload has no span to annualise. That must degrade to
    no rate/no divergence finding, not a ZeroDivisionError."""
    periods = pd.date_range("2021-01-01", periods=1, freq="MS")
    rows = [
        {"period": periods[0], "category": "C", "item_id": "a", "item_name": "a", "price_reported": 1.00},
        {"period": periods[0], "category": "C", "item_id": "b", "item_name": "b", "price_reported": 2.00},
    ]
    res = run_pipeline(_std(pd.DataFrame(rows)))
    nar = build_narrative(res)  # must not raise
    assert nar.headline


def test_single_category_has_no_divergence_finding():
    """A "diverges from X to X" finding comparing one category to itself is
    not falsifiable and must be suppressed, not published."""
    df = panel(n_items=3, n_periods=24, category="OnlyCategory")
    res = run_pipeline(df)
    findings = trend_findings(res["indices"], res["inflation"])
    assert not any("diverge" in f.headline for f in findings)


def test_quality_finding_does_not_claim_repair_when_dropped():
    """The headline finding must not say scale errors were "repaired" when
    the tool was configured to drop them instead."""
    df = panel(n_items=3, n_periods=24, seed=9)
    df.loc[df.index[10], "price_reported"] *= 100
    cfg = QualityConfig(repair_scale_errors=False)
    clean, q = run_quality(_std(df), cfg)
    findings = quality_findings(clean, q, RunConfig(quality=cfg))
    scale_finding = next(f for f in findings if "unit error" in f.headline)
    assert "recoverable" not in scale_finding.headline
    assert "dropped" in scale_finding.headline


def test_unmatched_comparison_handles_zero_base_price():
    """A zero base-period price must produce NaN, not inf: inf survives a
    plain .dropna() and would otherwise reach a published finding and chart."""
    periods = pd.date_range("2021-01-01", periods=2, freq="MS")
    rows = [
        {"period": periods[0], "category": "Zero", "item_id": "a", "price_imputed": 0.0},
        {"period": periods[1], "category": "Zero", "item_id": "a", "price_imputed": 5.0},
    ]
    I = pd.DataFrame({"Zero": [100.0, 110.0]}, index=periods)
    out = unmatched_comparison(pd.DataFrame(rows), I)
    assert np.isfinite(out["naive_mean_price"].dropna().to_numpy()).all()
    assert not np.isinf(out["naive_mean_price"]).any()


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------
def test_duplicate_key_fails_validation(clean_panel):
    df = _std(pd.concat([clean_panel, clean_panel.head(1)]))
    r = validate(df)
    assert not r.passed
    assert any("duplicate" in e for e in r.errors)


def test_negative_price_fails_validation(clean_panel):
    df = _std(clean_panel)
    df.loc[df.index[0], "price_reported"] = -1
    r = validate(df)
    assert not r.passed


def test_reused_item_id_warns():
    df = _std(panel(n_items=2, n_periods=4))
    df.loc[df.index[0], "item_name"] = "renamed"
    r = validate(df)
    assert any("more than one name" in w for w in r.warnings)


# ----------------------------------------------------------------------
# Reproducibility
# ----------------------------------------------------------------------
def test_config_roundtrips():
    import json
    c = RunConfig(label="test", index=IndexConfig(formula="dutot", chained=False))
    c2 = RunConfig.from_dict(json.loads(c.to_json()))
    assert c2.index.formula == "dutot"
    assert c2.index.chained is False
    assert c2.label == "test"


def test_same_config_gives_same_result(clean_panel):
    a = run_pipeline(clean_panel, RunConfig())["indices"]
    b = run_pipeline(clean_panel, RunConfig())["indices"]
    pd.testing.assert_frame_equal(a, b)


# ----------------------------------------------------------------------
def _std(df):
    from pricelab import standardise
    return standardise(df)
