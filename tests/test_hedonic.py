"""Hedonic regression: recovery of a known quality effect (Appendix 2,
test 6), the diagnostics that have to be there, and the multicollinearity
warning that has to fire.

No CPI Manual golden value is asserted here: Table 6.6's washing-machine
regression is illustrative and its dataset is not printed, so its
coefficients cannot be reproduced; see tests/test_quality_adjustment.py's
docstring. The recovery tests use `synthetic_hedonic_panel`, whose true
coefficients and period effects are known by construction.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest

from pricelab.engine import hedonic as h
from pricelab.engine import quality_adjustment as qa

TRUE = (0.4, -0.15)
BRAND_B = 0.1


@pytest.fixture(scope="module")
def panel() -> pd.DataFrame:
    return h.synthetic_hedonic_panel(n_items=80, n_periods=6, true_coefficients=TRUE,
                                     period_effects=[0.0, 0.01, 0.025, 0.03, 0.05, 0.06],
                                     noise_sd=0.04, seed=3)


@pytest.fixture(scope="module")
def spec() -> h.HedonicSpec:
    return h.HedonicSpec(characteristics=("z1", "z2"), categorical=("brand",),
                         functional_form="semi_log", weight_col="weight")


@pytest.fixture(scope="module")
def fit(panel, spec) -> h.HedonicResult:
    return h.fit_hedonic(panel, spec, stability_window=3, cv_folds=5)


# ---------------------------------------------------------------------
# Recovery of a known quality effect
# ---------------------------------------------------------------------
def test_the_estimator_recovers_the_true_characteristics_prices(fit):
    assert fit.coefficients["z1"] == pytest.approx(TRUE[0], abs=0.01)
    assert fit.coefficients["z2"] == pytest.approx(TRUE[1], abs=0.01)
    assert fit.coefficients["brand=B"] == pytest.approx(BRAND_B, abs=0.02)
    assert fit.adj_r_squared > 0.95


def test_a_known_quality_effect_is_recovered_within_tolerance_and_attributed(fit):
    """Appendix 2, test 6. The replacement has one more unit of z1 and is
    brand B where the old was A: its true quality ratio is exp(0.4 + 0.1).
    The hedonic adjustment must recover that within 2 percent, and the
    impact report must then attribute the effect to it."""
    old = {"z1": 2.0, "z2": 3.0, "brand": "A"}
    new = {"z1": 3.0, "z2": 3.0, "brand": "B"}
    true_ratio = float(np.exp(TRUE[0] + BRAND_B))
    adj = h.hedonic_adjustment(fit, "old", "new", "x", pd.Timestamp("2020-06-01"),
                               40.0, 40.0 * true_ratio * 1.02, old, new,
                               cell=qa.CellContext(n_items=10))
    assert adj.reason_code == "hedonic"
    assert adj.quality_ratio == pytest.approx(true_ratio, rel=0.02)
    # Of the collected +x%, the quality part is the ratio and the rest is price
    assert adj.pure_price_relative == pytest.approx(1.02, rel=0.02)
    assert adj.parameters["variant"] == "characteristics_price"
    assert adj.parameters["multicollinearity_warning"] is False
    assert np.isfinite(adj.index_points) and adj.index_points < 0

    # Attribution end to end: a panel where the replacement's price gap is
    # entirely the hedonic quality difference must come back with the
    # untouched series once the adjustment is applied.
    periods = pd.date_range("2020-01-01", periods=6, freq="MS")
    path = np.array([10.0, 10.2, 10.4, 10.6, 10.8, 11.0])
    rows = []
    for i, p in enumerate(periods):
        if i <= 3:
            rows.append({"period": p, "category": "x", "item_id": "old", "item_name": "o",
                         "price_reported": path[i]})
        if i >= 3:
            rows.append({"period": p, "category": "x", "item_id": "new", "item_name": "n",
                         "price_reported": path[i] * adj.quality_ratio})
        rows.append({"period": p, "category": "x", "item_id": "c", "item_name": "c",
                     "price_reported": 20.0 + 0.3 * i})
    df = pd.DataFrame(rows)
    from pricelab import run_pipeline
    from pricelab.core.config import IndexConfig, QualityAdjustmentConfig, RunConfig
    entry = adj.to_entry("tester").model_copy(update={"period": str(periods[4].date())})
    cfg = RunConfig(index=IndexConfig(min_matched_items=1),
                    quality_adjustment=QualityAdjustmentConfig(entries=[entry]))
    res = run_pipeline(df, cfg)
    imp = res["quality_adjustment_impact"]
    assert imp.per_entry.loc["old -> new", "method"] == "hedonic"
    assert imp.adjustment_effect_points == pytest.approx(
        imp.per_entry.loc["old -> new", "effect_points"] + imp.interaction_residual_points)
    # With the whole gap valued as quality, the linked series is the untouched path
    linked = res["clean"][res["clean"]["item_id"] == "old"].sort_values("period")
    np.testing.assert_allclose(linked["price_clean"].to_numpy(), path, rtol=1e-9)


def test_the_time_dummy_index_recovers_the_period_effects(fit):
    index = fit.time_dummy_index()
    expected = 100 * np.exp(np.array([0.0, 0.01, 0.025, 0.03, 0.05, 0.06]))
    np.testing.assert_allclose(index.to_numpy(), expected, rtol=0.01)
    corrected = fit.time_dummy_index(bias_corrected=True)
    assert (corrected.to_numpy()[1:] < index.to_numpy()[1:]).all()   # exp(d - V/2) < exp(d)
    assert corrected.iloc[0] == 100.0


def test_the_characteristics_price_index_recovers_the_period_effects(panel):
    spec = h.HedonicSpec(characteristics=("z1", "z2"), categorical=("brand",))
    panel_fit = h.fit_by_period(panel, spec)
    base = panel[panel["period"] == panel["period"].min()]
    bundles = {p: g[["z1", "z2", "brand"]] for p, g in panel.groupby("period")}
    table = panel_fit.characteristics_price_index(base[["z1", "z2", "brand"]],
                                                  current_bundles=bundles)
    expected = 100 * np.exp(np.array([0.0, 0.01, 0.025, 0.03, 0.05, 0.06]))
    for col in ("laspeyres_type", "paasche_type", "fisher_type"):
        np.testing.assert_allclose(table[col].to_numpy(), expected, rtol=0.01)


def test_hedonic_imputation_recovers_the_pure_price_change(panel):
    spec = h.HedonicSpec(characteristics=("z1", "z2"), categorical=("brand",))
    panel_fit = h.fit_by_period(panel, spec)
    old = {"z1": 2.0, "z2": 3.0, "brand": "A"}
    t0, t1 = pd.Timestamp("2020-01-01"), pd.Timestamp("2020-02-01")
    adj = h.hedonic_imputation_adjustment(panel_fit, "old", "new", "x", t0, t1, 40.0, 50.0, old)
    assert adj.parameters["variant"] == "imputation"
    assert adj.parameters["double_imputation"] is True
    assert adj.parameters["imputed_price_relative"] == pytest.approx(np.exp(0.01), abs=0.01)
    assert adj.quality_ratio == pytest.approx((50.0 / 40.0) / np.exp(0.01), rel=0.02)
    single = h.hedonic_imputation_adjustment(panel_fit, "old", "new", "x", t0, t1, 40.0, 50.0, old,
                                             double=False)
    assert single.parameters["double_imputation"] is False
    assert single.parameters["imputed_price_relative"] != adj.parameters["imputed_price_relative"]


# ---------------------------------------------------------------------
# Functional forms and weights
# ---------------------------------------------------------------------
def test_log_linear_semi_log_and_box_cox_all_fit_and_predict_positive_prices(panel):
    for form in h.FUNCTIONAL_FORMS:
        spec = h.HedonicSpec(characteristics=("z1", "z2"), categorical=("brand",),
                             functional_form=form)
        r = h.fit_hedonic(panel, spec, cv_folds=3)
        assert r.adj_r_squared > 0.8
        pred = r.predict({"z1": 2.0, "z2": 3.0, "brand": "A"})
        assert pred.iloc[0] > 0
        if form == "box_cox":
            assert r.box_cox_lambda is not None and -2.0 <= r.box_cox_lambda <= 2.0
        else:
            assert r.box_cox_lambda is None


def test_box_cox_lambda_can_be_fixed(panel):
    spec = h.HedonicSpec(characteristics=("z1", "z2"), functional_form="box_cox", box_cox_lambda=0.5)
    r = h.fit_hedonic(panel, spec, cv_folds=0)
    assert r.box_cox_lambda == 0.5


def test_log_linear_refuses_non_positive_characteristics(panel):
    bad = panel.copy()
    bad.loc[bad.index[0], "z1"] = 0.0
    with pytest.raises(h.HedonicError, match="non-positive"):
        h.fit_hedonic(bad, h.HedonicSpec(characteristics=("z1",), functional_form="log_linear"))


def test_expenditure_weights_change_the_fit(panel):
    weighted = panel.copy()
    weighted["weight"] = np.where(weighted["brand"] == "B", 50.0, 1.0)
    unweighted = h.fit_hedonic(panel, h.HedonicSpec(characteristics=("z1",)), cv_folds=0)
    wls = h.fit_hedonic(weighted, h.HedonicSpec(characteristics=("z1",), weight_col="weight"),
                        cv_folds=0)
    assert wls.coefficients["const"] != pytest.approx(unweighted.coefficients["const"], abs=1e-6)
    with pytest.raises(h.HedonicError, match="positive"):
        bad = weighted.copy()
        bad["weight"] = 0.0
        h.fit_hedonic(bad, h.HedonicSpec(characteristics=("z1",), weight_col="weight"))


# ---------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------
def test_the_diagnostics_are_all_reported(fit):
    table = fit.summary_frame()
    assert set(table.columns) == {"coefficient", "robust_se", "p_value", "vif"}
    assert (table["robust_se"] > 0).all()
    assert fit.vif.drop([c for c in fit.vif.index if c.startswith("period=")]).max() < 5
    assert np.isfinite(fit.condition_number)
    assert len(fit.residuals) == len(fit.fitted) == len(fit.leverage) == fit.n_obs
    assert fit.leverage.between(0, 1).all()
    assert fit.leverage.sum() == pytest.approx(fit.n_params, rel=1e-6)   # trace of the hat matrix
    assert fit.coefficient_stability is not None
    assert "range" in fit.coefficient_stability.index
    assert (fit.coefficient_stability.loc["range"].abs() < 0.05).all()
    assert fit.out_of_sample["folds"] == 5
    assert 0 < fit.out_of_sample["mape_pct"] < 10
    assert fit.warnings == []


def test_severe_multicollinearity_warns_rather_than_reporting_silently(panel):
    """Acceptance criterion. z3 is z1 doubled: the coefficients on the two
    are not separately identified, and the fit must say so."""
    collinear = panel.copy()
    collinear["z3"] = collinear["z1"] * 2.0 + 1e-9
    spec = h.HedonicSpec(characteristics=("z1", "z2", "z3"))
    with pytest.warns(h.HedonicMulticollinearityWarning, match="severe multicollinearity"):
        r = h.fit_hedonic(collinear, spec, cv_folds=0)
    assert r.warnings and "z1" in r.warnings[0] and "z3" in r.warnings[0]
    assert not np.isfinite(r.vif["z1"]) or r.vif["z1"] > spec.vif_threshold
    # The flag travels with any adjustment read from the fit
    adj = h.hedonic_adjustment(r, "a", "b", "x", pd.Timestamp("2020-06-01"), 40.0, 50.0,
                               {"z1": 2.0, "z2": 3.0, "z3": 4.0}, {"z1": 3.0, "z2": 3.0, "z3": 6.0})
    assert adj.parameters["multicollinearity_warning"] is True



def test_the_multicollinearity_warning_names_whichever_test_fired():
    """The message used to read "severe multicollinearity: none above
    threshold" when only the design condition number tripped, which
    contradicts itself and leaves the reader unable to tell which diagnostic
    to look at."""
    rng = np.random.default_rng(77)
    n = 200
    # One characteristic on a scale far from the others: the condition number
    # rises without any individual VIF doing so.
    size = rng.uniform(1, 4, n)
    volume = rng.uniform(900, 1100, n)
    frame = pd.DataFrame({
        "period": np.repeat(pd.date_range("2022-01-01", periods=4, freq="MS"), n // 4),
        "item_id": [f"I{i}" for i in range(n)],
        "price": np.exp(1 + 0.2 * size + 0.0002 * volume + rng.normal(0, 0.05, n)),
        "size": size, "volume": volume})
    spec = h.HedonicSpec(characteristics=("size", "volume"), functional_form="semi_log",
                         condition_threshold=2.0)
    with pytest.warns(h.HedonicMulticollinearityWarning) as caught:
        h.fit_hedonic(frame, spec, variant="time_dummy")
    message = str(caught[0].message)
    assert "none above threshold" not in message
    assert "design condition number of" in message
    assert "no individual VIF above" in message

def test_moderate_collinearity_below_threshold_does_not_warn(panel):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        r = h.fit_hedonic(panel, h.HedonicSpec(characteristics=("z1", "z2")), cv_folds=0)
    assert r.warnings == []


def test_too_few_observations_is_refused(panel):
    tiny = panel[panel["period"] == panel["period"].min()].head(3)
    with pytest.raises(h.HedonicError, match="cannot estimate"):
        h.fit_hedonic(tiny, h.HedonicSpec(characteristics=("z1", "z2")), time_dummies=False)


def test_spec_validation():
    with pytest.raises(h.HedonicError, match="functional_form"):
        h.HedonicSpec(characteristics=("z1",), functional_form="cubic")
    with pytest.raises(h.HedonicError, match="at least one characteristic"):
        h.HedonicSpec()


def test_predict_with_an_unseen_categorical_level_uses_the_reference_level(fit):
    """A level the fit never saw has no coefficient; it is priced at the
    reference level rather than crashing, which is what a reviewer would
    expect a dummy-encoded model to do."""
    a = fit.predict({"z1": 2.0, "z2": 3.0, "brand": "A"}).iloc[0]
    z = fit.predict({"z1": 2.0, "z2": 3.0, "brand": "Z"}).iloc[0]
    assert a == pytest.approx(z)


def test_time_dummy_index_is_only_for_the_time_dummy_variant(panel):
    r = h.fit_hedonic(panel, h.HedonicSpec(characteristics=("z1",)), time_dummies=False,
                      variant="characteristics_price", cv_folds=0)
    with pytest.raises(h.HedonicError, match="time_dummy"):
        r.time_dummy_index()
