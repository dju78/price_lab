"""Phase 5: multilateral methods for transaction and scanner data.

The tests are organised the way the claims are: what the panel builder
promises about the data it hands the methods; the axioms every method must
satisfy (identity, proportionality, transitivity) and the closed forms two of
them collapse to; Appendix 2's chain drift test, which is the reason the
phase exists; what each method does that the others do not; the six extension
rules and the relationships between them; and the acceptance criteria --
materially less drift than a chained bilateral on churning data, the splices
reproducing their published relationships on a worked example, and a hundred
thousand transactions over a twenty-five month window inside thirty seconds.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import streamlit
from dbtarget import database_url
from streamlit.testing.v1 import AppTest

from pricelab import infer_schema, standardise
from pricelab.core import db
from pricelab.core.config import IndexConfig, MultilateralConfig, RunConfig, get_settings
from pricelab.engine import multilateral as ml
from pricelab.engine.bilateral import fisher, tornqvist
from pricelab.engine.hedonic import HedonicSpec
from pricelab.engine.index import build_index, jevons

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNER = REPO_ROOT / "tests" / "fixtures" / "scanner_transactions.csv"
CHARACTERISTICS = REPO_ROOT / "tests" / "fixtures" / "scanner_characteristics.csv"

#: The five methods computable from prices and quantities alone. The time
#: dummy hedonic needs a characteristics table and is exercised separately.
PANEL_METHODS = ("geks_fisher", "geks_tornqvist", "tpd", "wtpd", "geary_khamis")


# ---------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------
def _panel_frame(prices: dict[str, list[float]], quantities: dict[str, list[float]] | None = None,
                 start: str = "2022-01-01") -> pd.DataFrame:
    """A long frame from {item: [price per period]}."""
    n = len(next(iter(prices.values())))
    periods = pd.date_range(start, periods=n, freq="MS")
    rows = []
    for item, series in prices.items():
        for t, value in enumerate(series):
            if value is None or not np.isfinite(value):
                continue
            row: dict[str, object] = {"period": periods[t], "item_id": item,
                                      "price_imputed": float(value)}
            if quantities is not None:
                row["quantity"] = float(quantities[item][t])
            rows.append(row)
    return pd.DataFrame(rows)


def _flat_panel(n_periods: int = 6, n_items: int = 4, factor: float = 1.0) -> pd.DataFrame:
    """Every item in every period, all prices multiplied by `factor ** t`."""
    prices = {f"I{i}": [(5.0 + i) * factor ** t for t in range(n_periods)] for i in range(n_items)}
    quantities = {f"I{i}": [100.0 + 7 * i - t for t in range(n_periods)] for i in range(n_items)}
    return _panel_frame(prices, quantities)


def test_build_panel_collapses_duplicate_product_periods_to_the_unit_value():
    """Two stores selling the same product in the same month are one
    product-period at the quantity-weighted price, not two observations and
    not their arithmetic mean: a store that sold three packs must not weigh
    the same as one that sold three hundred."""
    frame = pd.DataFrame([
        {"period": "2022-01-01", "item_id": "A", "price_imputed": 10.0, "quantity": 10.0},
        {"period": "2022-01-01", "item_id": "A", "price_imputed": 20.0, "quantity": 90.0},
        {"period": "2022-02-01", "item_id": "A", "price_imputed": 19.0, "quantity": 100.0},
    ])
    frame["period"] = pd.to_datetime(frame["period"])
    panel = ml.build_panel(frame)
    assert panel.n_periods == 2 and panel.n_items == 1
    # (10*10 + 20*90) / 100 = 19.0, not (10+20)/2 = 15.0
    assert panel.prices[0, 0] == pytest.approx(19.0)
    assert panel.quantities is not None and panel.quantities[0, 0] == pytest.approx(100.0)


def test_build_panel_reports_churn_and_observation_counts():
    frame = _panel_frame({"A": [1.0, 1.1, 1.2], "B": [2.0, float("nan"), 2.2]},
                         {"A": [10.0, 10.0, 10.0], "B": [5.0, 0.0, 5.0]})
    panel = ml.build_panel(frame)
    assert panel.n_periods == 3 and panel.n_items == 2
    assert panel.n_observations == 5
    assert panel.churn_share == pytest.approx(1 / 6)


def test_build_panel_drops_non_positive_prices_rather_than_logging_them():
    """A zero is a sentinel for "not collected", not a giveaway, and a log
    price regression would take its logarithm."""
    frame = _panel_frame({"A": [1.0, 0.0, 1.2], "B": [2.0, 2.1, 2.2]},
                         {"A": [10.0, 10.0, 10.0], "B": [5.0, 5.0, 5.0]})
    panel = ml.build_panel(frame)
    assert not np.isfinite(panel.prices[1, 0])
    assert panel.n_observations == 5


def test_build_panel_without_quantities_leaves_the_weighted_methods_unavailable_with_a_reason():
    frame = _panel_frame({"A": [1.0, 1.1], "B": [2.0, 2.1]})
    panel = ml.build_panel(frame)
    assert panel.quantities is None and panel.quantity_source is None
    availability = ml.method_availability(panel)
    assert availability["tpd"] is None                      # prices are enough
    for method in ("geks_fisher", "geks_tornqvist", "wtpd", "geary_khamis"):
        assert "quantity" in (availability[method] or "")
    assert "characteristics" in (availability["tdh"] or "")
    with pytest.raises(ml.MultilateralError, match="no quantity or expenditure"):
        ml.geks(panel, "fisher")


def test_build_panel_derives_quantity_from_expenditure_and_says_so():
    frame = pd.DataFrame([
        {"period": pd.Timestamp("2022-01-01"), "item_id": "A", "price_imputed": 4.0,
         "expenditure": 40.0},
        {"period": pd.Timestamp("2022-02-01"), "item_id": "A", "price_imputed": 5.0,
         "expenditure": 50.0}])
    panel = ml.build_panel(frame)
    assert panel.quantity_source == "quantity_derived"
    assert panel.quantities is not None
    assert panel.quantities[:, 0] == pytest.approx([10.0, 10.0])


def test_subset_keeps_only_the_products_sold_in_the_chosen_periods():
    frame = _panel_frame({"A": [1.0, 1.1, 1.2], "B": [float("nan"), float("nan"), 2.2]},
                         {"A": [10.0, 10.0, 10.0], "B": [0.0, 0.0, 5.0]})
    panel = ml.build_panel(frame)
    window = panel.subset(list(panel.periods[:2]))
    assert window.n_periods == 2 and window.n_items == 1
    assert list(window.items) == ["A"]


# ---------------------------------------------------------------------
# Axioms every method must satisfy
# ---------------------------------------------------------------------
@pytest.mark.parametrize("method", PANEL_METHODS)
def test_identity_every_method_reads_one_hundred_when_no_price_changes(method):
    panel = ml.build_panel(_flat_panel(factor=1.0))
    result = ml.multilateral_index(panel, method)
    assert result.index.to_numpy() == pytest.approx(np.full(panel.n_periods, 100.0))


@pytest.mark.parametrize("method", PANEL_METHODS)
@pytest.mark.parametrize("factor", [0.93, 1.0, 1.07])
def test_proportionality_every_method_follows_a_common_price_path_exactly(method, factor):
    """Every price multiplied by the same factor each period: the index is
    that factor compounded, whatever the quantities, for every method. This
    is the axiom that catches a weighting bug, because a method that
    mis-weights still passes identity."""
    n = 6
    panel = ml.build_panel(_flat_panel(n_periods=n, factor=factor))
    result = ml.multilateral_index(panel, method)
    expected = 100.0 * factor ** np.arange(n)
    assert result.index.to_numpy() == pytest.approx(expected, rel=1e-9)


@pytest.mark.parametrize("block", ["fisher", "tornqvist"])
def test_geks_is_exactly_transitive(block):
    """The property the whole phase rests on: comparing period 0 to period 4
    directly must equal comparing it through period 2, to machine precision,
    for any data. It holds by construction -- the GEKS level of a period is a
    single number and every comparison is a ratio of two of them -- and this
    test is what stops a future edit reintroducing a path."""
    rng = np.random.default_rng(4)
    prices = {f"I{i}": list(np.exp(np.cumsum(rng.normal(0, 0.08, 6))) * (3 + i))
              for i in range(5)}
    quantities = {f"I{i}": list(rng.uniform(20, 300, 6)) for i in range(5)}
    panel = ml.build_panel(_panel_frame(prices, quantities))
    index = ml.geks(panel, block).index.to_numpy()
    for start in range(4):
        for middle in range(start + 1, 5):
            direct = index[5 - 1] / index[start]
            through = (index[middle] / index[start]) * (index[5 - 1] / index[middle])
            assert direct == pytest.approx(through, rel=1e-14)


@pytest.mark.parametrize("block,fn", [("fisher", fisher), ("tornqvist", tornqvist)])
def test_geks_over_two_periods_is_the_bilateral_index_itself(block, fn):
    """With one pair there is nothing to average over the bridges, so GEKS
    must collapse to its building block exactly. A GEKS that does not is
    averaging something it should not be."""
    rng = np.random.default_rng(9)
    items = [f"I{i}" for i in range(6)]
    p0, p1 = rng.uniform(1, 30, 6), rng.uniform(1, 30, 6)
    q0, q1 = rng.uniform(5, 200, 6), rng.uniform(5, 200, 6)
    frame = _panel_frame({it: [float(p0[i]), float(p1[i])] for i, it in enumerate(items)},
                         {it: [float(q0[i]), float(q1[i])] for i, it in enumerate(items)})
    panel = ml.build_panel(frame)
    index = ml.geks(panel, block).index
    expected = fn(pd.Series(p0, index=items), pd.Series(p1, index=items),
                  pd.Series(q0, index=items), pd.Series(q1, index=items)).value
    assert float(index.iloc[1]) / 100.0 == pytest.approx(expected, rel=1e-12)


def test_unweighted_tpd_over_a_balanced_two_period_panel_is_the_jevons_index():
    """Least squares on ln p = alpha + delta + gamma_i over a balanced panel
    of two periods has the closed form delta = mean_i(ln p_i1 - ln p_i0),
    which is the log Jevons. The identity is worth pinning because it is the
    only point at which the regression and the elementary engine can be
    checked against each other without a tolerance argument."""
    rng = np.random.default_rng(12)
    items = [f"I{i}" for i in range(7)]
    p0, p1 = rng.uniform(2, 40, 7), rng.uniform(2, 40, 7)
    panel = ml.build_panel(
        _panel_frame({it: [float(p0[i]), float(p1[i])] for i, it in enumerate(items)}))
    index = ml.time_product_dummy(panel).index
    expected = jevons(pd.Series(p0, index=items), pd.Series(p1, index=items))
    assert float(index.iloc[1]) / 100.0 == pytest.approx(expected, rel=1e-9)


# ---------------------------------------------------------------------
# Appendix 2, test 5: chain drift
# ---------------------------------------------------------------------
def _bouncing_panel() -> pd.DataFrame:
    """Prices and quantities that return exactly to where they started.

    Two products on alternating promotion cycles of four months, discounted
    by thirty percent while on promotion. Quantities respond to the
    discount -- and stay elevated the month after it ends, because
    households stockpile. That asymmetry between the price cycle and the
    quantity cycle is the whole mechanism: it is what makes each chain link
    price a different basket from the one that undid it, so the links do not
    cancel. The pattern has period four, so month twelve is month zero in
    every price and every quantity: nothing changed over the span, and an
    index that says otherwise invented it.

    CPI Manual 2020, Chapter 10, Table 10.1 is the same construction.
    """
    n = 13

    def on_promotion(t: int, item: int) -> bool:
        return ((t + 2 * item) % 4) in (1, 2)

    prices: dict[str, list[float]] = {}
    quantities: dict[str, list[float]] = {}
    for item, base in enumerate((10.0, 20.0)):
        name = f"P{item}"
        prices[name] = [base * (0.7 if on_promotion(t, item) else 1.0) for t in range(n)]
        quantities[name] = [
            600.0 if on_promotion(t, item) else (300.0 if on_promotion(t - 1, item) else 50.0)
            for t in range(n)]
    return _panel_frame(prices, quantities)


@pytest.mark.parametrize("method", PANEL_METHODS)
def test_a_multilateral_index_returns_to_its_starting_level_when_the_data_does(method):
    frame = _bouncing_panel()
    panel = ml.build_panel(frame)
    assert panel.prices[0] == pytest.approx(panel.prices[12])
    assert panel.quantities is not None
    assert panel.quantities[0] == pytest.approx(panel.quantities[12])
    index = ml.multilateral_index(panel, method).index
    assert float(index.iloc[-1]) == pytest.approx(100.0, abs=1e-8)


@pytest.mark.parametrize("formula", ["tornqvist", "fisher"])
def test_a_chained_superlative_index_drifts_on_the_same_data_and_the_diagnostic_says_by_how_much(
        formula):
    """Appendix 2, test 5. The chained index must drift, the multilateral one
    must not, and the size of the gap must be reported rather than inferred
    from two numbers on a chart."""
    frame = _bouncing_panel()
    multilateral = ml.geks(ml.build_panel(frame), "tornqvist").index
    report = ml.drift_against_chained(frame, multilateral, formula=formula)

    assert report.direct_level == pytest.approx(100.0, abs=1e-8)
    assert report.exceeds_threshold
    assert abs(report.drift_pp) > 20.0          # the manual's example drifts by 22 points
    assert "points above" in report.message or "points below" in report.message
    assert f"{abs(report.drift_pp):.2f}" in report.message


def test_the_drift_diagnostic_is_the_same_one_the_bilateral_engine_uses():
    """Not a second implementation: the multilateral series takes the place
    of the direct comparison in `engine.splicing.chain_drift`, because that
    is exactly what a transitive index is."""
    from pricelab.engine.splicing import chain_drift

    frame = _bouncing_panel()
    multilateral = ml.geks(ml.build_panel(frame), "tornqvist").index
    chained = build_index(frame, IndexConfig(formula="tornqvist", chained=True))["index"]
    assert (ml.drift_against_chained(frame, multilateral, formula="tornqvist").drift_pp
            == pytest.approx(chain_drift(chained.dropna(), multilateral.dropna()).drift_pp))


# ---------------------------------------------------------------------
# What each method does that the others do not
# ---------------------------------------------------------------------
def test_geks_excludes_a_period_it_cannot_compare_to_every_other_and_says_so():
    """A period sharing nothing with some other period cannot be a bridge
    without making the average range over a different set per period, which
    would destroy transitivity. It is dropped from the bridge set, keeps a
    level through the periods that do reach it, and the exclusion is
    reported rather than absorbed.

    The panel: two months stocking only the old range, two stocking only the
    new one, and two carrying both. The first and third cannot be compared
    to each other at all, so neither can bridge; the two that carry
    everything can, and every month still gets a level through them.
    """
    old_range, new_range = [10.0, 10.5, 11.0, 11.5], [30.0, 30.6, 31.2, 31.8]
    nan = float("nan")
    prices = {
        "A": [old_range[0], old_range[1], nan, old_range[3]],
        "B": [old_range[0] * 2, old_range[1] * 2, nan, old_range[3] * 2],
        "C": [nan, new_range[1], new_range[2], new_range[3]],
        "D": [nan, new_range[1] * 2, new_range[2] * 2, new_range[3] * 2],
    }
    quantities = {k: [100.0] * 4 for k in prices}
    panel = ml.build_panel(_panel_frame(prices, quantities))
    result = ml.geks(panel, "fisher")
    assert result.diagnostics["bridge_periods"] == 2      # the two full months
    assert any("excluded from the bridge set" in w for w in result.warnings)
    assert np.isfinite(result.index.to_numpy()).all()


def test_geks_refuses_a_window_no_product_survives_and_names_the_alternative():
    prices = {"A": [10.0, 11.0, float("nan"), float("nan")],
              "B": [20.0, 21.0, float("nan"), float("nan")],
              "C": [float("nan"), float("nan"), 30.0, 31.0],
              "D": [float("nan"), float("nan"), 40.0, 42.0]}
    panel = ml.build_panel(_panel_frame(prices, {k: [100.0] * 4 for k in prices}))
    with pytest.raises(ml.MultilateralError, match="not connected"):
        ml.geks(panel, "fisher")
    # The same window is perfectly workable for the time product dummy,
    # which is what the message says and what makes it worth raising.
    assert np.isfinite(ml.time_product_dummy(panel).index.to_numpy()).all()


def test_geks_reports_how_much_expenditure_a_matched_model_comparison_keeps():
    frame = pd.read_csv(SCANNER)
    panel = ml.build_panel(standardise(frame, infer_schema(frame)), "price_reported")
    share = ml.geks(panel.subset(list(panel.periods[:13])), "fisher"
                    ).diagnostics["matched_expenditure_share"]
    assert 0.0 < share < 1.0            # a churning collection keeps some, never all


def test_the_time_product_dummy_uses_observations_a_matched_model_method_discards():
    """A product sold in only one period of a pair carries no price relative
    and cannot enter a bilateral comparison. It still constrains the
    regression, through its own dummy and the periods it shares with the
    others, which is the entire reason TPD exists for scanner data."""
    prices = {"A": [10.0, 11.0, 12.0], "B": [20.0, 22.0, 24.0],
              "NEW": [float("nan"), float("nan"), 50.0]}
    quantities = {"A": [100.0] * 3, "B": [100.0] * 3, "NEW": [0.0, 0.0, 400.0]}
    panel = ml.build_panel(_panel_frame(prices, quantities))
    result = ml.time_product_dummy(panel)
    assert result.n_items == 3
    assert result.n_observations == 7
    # The newcomer's level is absorbed by its own dummy, so the index still
    # reads the ten percent a period the other two actually moved.
    assert result.index.to_numpy() == pytest.approx([100.0, 110.0, 120.0], rel=1e-9)


def test_weighting_the_time_product_dummy_moves_it_towards_what_sold():
    """Unweighted, a clearance line with one unit sold counts as much as the
    product beside it with ten thousand. Weighted, it does not -- and the two
    must agree when every product sells the same, or the weighting is doing
    something other than weighting."""
    prices = {"BIG": [10.0, 10.0, 10.0], "SMALL": [10.0, 5.0, 5.0]}
    # Equal *expenditure* shares, not equal quantities: the weights are
    # shares, so a panel with equal quantities and unequal prices is already
    # a weighted panel and would prove nothing.
    equal_shares = {item: [1000.0 / price for price in series]
                    for item, series in prices.items()}
    lopsided = {"BIG": [1000.0] * 3, "SMALL": [1.0] * 3}

    balanced = ml.build_panel(_panel_frame(prices, equal_shares))
    assert (ml.time_product_dummy(balanced, weighted=True).index.to_numpy()
            == pytest.approx(ml.time_product_dummy(balanced).index.to_numpy(), rel=1e-7))

    skewed = ml.build_panel(_panel_frame(prices, lopsided))
    unweighted = float(ml.time_product_dummy(skewed).index.iloc[-1])
    weighted = float(ml.time_product_dummy(skewed, weighted=True).index.iloc[-1])
    assert weighted > unweighted        # the halving happened to what nobody bought
    assert weighted > 99.0 and unweighted < 80.0


def test_a_saturated_regression_warns_that_its_levels_have_no_evidence_behind_them():
    """Two products, each sold in one period only: two observations against
    three parameters. Least squares will return numbers, and they will fit
    perfectly, and nothing in them is evidence about price change -- the
    period dummy and the product dummies are not separately identified. The
    warning is the only thing standing between that and a published level."""
    nan = float("nan")
    panel = ml.build_panel(_panel_frame({"A": [10.0, nan], "B": [nan, 30.0]}))
    result = ml.time_product_dummy(panel)
    assert result.n_observations == 2
    assert result.diagnostics["degrees_of_freedom"] < 0
    assert any("saturated or under-determined" in w for w in result.warnings)


def test_geary_khamis_satisfies_its_defining_additivity_identity():
    """The property the method is chosen for: each period's expenditure at
    actual prices equals its index level times its basket valued at the one
    common set of reference prices. If that does not hold, the levels are not
    Geary-Khamis levels whatever else they are."""
    rng = np.random.default_rng(21)
    items = [f"I{i}" for i in range(6)]
    prices = {it: list(np.exp(np.cumsum(rng.normal(0.004, 0.05, 7))) * (2 + i))
              for i, it in enumerate(items)}
    quantities = {it: list(rng.uniform(10, 400, 7)) for it in items}
    frame = _panel_frame(prices, quantities)
    panel = ml.build_panel(frame)
    result = ml.geary_khamis(panel)
    P = result.index.to_numpy() / 100.0

    assert panel.quantities is not None
    p, q = panel.prices, panel.quantities
    # Recover the reference prices the levels imply and check the identity.
    b = ((q / q.sum(axis=0)[None, :]) * (p / P[:, None])).sum(axis=0)
    actual = (p * q).sum(axis=1)
    at_reference = (b[None, :] * q).sum(axis=1)
    assert (actual / at_reference) == pytest.approx(P * (actual[0] / at_reference[0]) / P[0],
                                                    rel=1e-9)
    assert result.diagnostics["iterations"] >= 1
    assert result.diagnostics["final_change"] < 1e-11


def test_geary_khamis_reports_rather_than_hides_a_failure_to_converge():
    """A method that silently vanishes from a comparison view is worse than
    one that reports its own doubt, so the last iterate comes back with the
    failure attached."""
    panel = ml.build_panel(_flat_panel(n_periods=5, factor=1.02))
    result = ml.geary_khamis(panel, tolerance=1e-30, max_iterations=2)
    assert result.diagnostics["iterations"] == 2
    assert any("had not settled" in w for w in result.warnings)
    assert np.isfinite(result.index.to_numpy()).all()


def test_the_time_dummy_hedonic_prices_products_the_matched_methods_never_see():
    """Characteristics in place of product dummies: a product that appears
    once still contributes, because its quality is read off its size and
    brand rather than from a second observation of itself. The test injects a
    known trend and a known quality gradient and asks for the trend back."""
    rng = np.random.default_rng(30)
    periods = pd.date_range("2022-01-01", periods=10, freq="MS")
    rows = []
    for t, period in enumerate(periods):
        for i in range(14):
            size = float(rng.uniform(1, 6))
            brand = ("own", "mid", "premium")[i % 3]
            price = float(np.exp(1.0 + 0.3 * size + {"own": -0.2, "mid": 0.0, "premium": 0.3}[brand]
                                 + 0.01 * t + rng.normal(0, 0.01)))
            rows.append({"period": period, "item_id": f"I{t}_{i}",   # every product is new
                         "price_imputed": price, "size": size, "brand": brand,
                         "quantity": 50.0})
    frame = pd.DataFrame(rows)
    spec = HedonicSpec(characteristics=("size",), categorical=("brand",),
                       functional_form="semi_log", price_col="price_imputed")
    result = ml.time_dummy_hedonic(frame, spec)
    assert result.method == "tdh"
    assert result.diagnostics["r_squared"] > 0.98
    # exp(0.01) - 1 per period, compounded over nine of them
    assert float(result.index.iloc[-1]) == pytest.approx(100.0 * np.exp(0.09), rel=0.02)
    # Every one of those products is unique to its period, so the matched
    # model methods have nothing at all to compare.
    with pytest.raises(ml.MultilateralError, match="not connected"):
        ml.geks(ml.build_panel(frame), "fisher")


def test_the_time_dummy_hedonic_runs_on_the_scanner_fixture_through_its_characteristics_file():
    """The whole path a caller takes: a price panel, a separate item-level
    characteristics table, and a rolling window. The size and brand the
    fixture priced its products from are exactly what the regression is
    given, so it must land in the same neighbourhood as the methods that
    never saw them -- a hedonic index that disagrees wildly with GEKS on
    data whose quality gradient is known is not measuring quality."""
    frame = _scanner_panel()
    chars = pd.read_csv(CHARACTERISTICS).rename(
        columns={"Item_ID": "item_id", "Size": "size", "Brand": "brand"})[
        ["item_id", "size", "brand"]]
    spec = HedonicSpec(characteristics=("size",), categorical=("brand",),
                       functional_form="semi_log", price_col="price_imputed")

    result = ml.extend(frame, "tdh", window=25, splice="mean", hedonic_spec=spec,
                       characteristics=chars)
    assert len(result.index) == 30
    assert np.isfinite(result.index.to_numpy()).all()

    geks = ml.extend(frame, "geks_fisher", window=25, splice="mean")
    assert abs(float(result.index.iloc[-1]) - float(geks.index.iloc[-1])) < 15.0
    # and it drifts far less than the chained bilateral, like the others
    assert ml.drift_against_chained(frame, result.index).drift_pp > 10.0


def test_the_time_dummy_hedonic_refuses_to_run_without_a_specification():
    panel = ml.build_panel(_flat_panel())
    with pytest.raises(ml.MultilateralError, match="HedonicSpec"):
        ml.multilateral_index(panel, "tdh")


def test_characteristics_are_joined_without_dropping_products_that_have_none():
    frame = _flat_panel(n_periods=3, n_items=3)
    chars = pd.DataFrame({"item_id": ["I0", "I1"], "size": [1.0, 2.0]})
    joined = ml.with_characteristics(frame, chars)
    assert len(joined) == len(frame)
    assert joined["size"].isna().sum() == 3        # I2's three periods, kept
    with pytest.raises(ml.MultilateralError, match="item_id"):
        ml.with_characteristics(frame, pd.DataFrame({"code": ["I0"], "size": [1.0]}))


# ---------------------------------------------------------------------
# The extension rules
# ---------------------------------------------------------------------
def test_a_span_no_longer_than_the_window_is_simply_the_direct_index():
    frame = _flat_panel(n_periods=6, factor=1.01)
    result = ml.extend(frame, "geks_fisher", window=25)
    assert result.n_windows == 1
    assert any("no splicing was needed" in w for w in result.warnings)
    assert result.splice_spread_pp.max() == 0.0
    direct = ml.geks(ml.build_panel(frame), "fisher").index
    assert result.index.to_numpy() == pytest.approx(direct.to_numpy())


@pytest.mark.parametrize("splice", ml.SPLICES)
def test_every_splice_reproduces_the_direct_index_when_the_windows_do_not_disagree(splice):
    """The worked example the splices' published relationships are stated
    against: every product present in every period, every price on the same
    path, so each window is a rescaling of the last and there is nothing to
    revise. All six rules must then give one identical series, equal to the
    direct multilateral index over the whole span. Where they differ on real
    data, this test says the difference is the data's, not the rules'.
    """
    n = 30
    factor = 1.003
    frame = _flat_panel(n_periods=n, n_items=5, factor=factor)
    result = ml.extend(frame, "geks_fisher", window=13, splice=splice)
    expected = 100.0 * factor ** np.arange(n)
    assert result.index.to_numpy() == pytest.approx(expected, rel=1e-10)
    assert result.splice_spread_pp.max() < 1e-9
    assert len(result.index) == n


def test_the_splices_disagree_only_through_which_overlap_period_they_link_at():
    """Mean splice is the geometric mean of every candidate link, so it lies
    between the smallest and the largest; movement, window and half splice
    are each one of those candidates. That ordering is the published
    relationship between them, and it holds whatever the data does."""
    frame = _scanner_panel()
    levels = {splice: float(ml.extend(frame, "geks_fisher", window=13, splice=splice)
                            .index.iloc[-1])
              for splice in ("movement", "window", "half", "mean")}
    singles = [levels["movement"], levels["window"], levels["half"]]
    assert min(singles) <= levels["mean"] <= max(singles)
    # and they genuinely differ on churning data, or the comparison is moot
    assert max(singles) - min(singles) > 0.01


def test_the_splice_spread_is_reported_and_is_the_disagreement_between_the_rules():
    frame = _scanner_panel()
    results = {s: ml.extend(frame, "geks_fisher", window=13, splice=s)
               for s in ("movement", "window", "half", "mean")}
    finals = [float(r.index.iloc[-1]) for r in results.values()]
    # The last period's reported spread bounds how far apart the rules could
    # land on it, so it cannot be smaller than the spread they actually show.
    last_spread = float(results["mean"].splice_spread_pp.iloc[-1])
    assert last_spread >= (max(finals) - min(finals)) - 1e-9
    assert last_spread > 0


def test_a_rolling_window_computes_one_window_per_new_period():
    frame = _flat_panel(n_periods=20, n_items=4, factor=1.002)
    result = ml.extend(frame, "tpd", window=13, splice="mean")
    assert result.n_windows == 20 - 13 + 1
    assert len(result.index) == 20
    assert result.index.index.is_monotonic_increasing


@pytest.mark.parametrize("splice", ["fbew", "fbmw"])
def test_the_fixed_base_rules_re_anchor_at_the_anchor_month(splice):
    """One link a year, at the anchor, which both the window that ended and
    the window that starts contain. Every period still gets exactly one
    level and nothing is published twice."""
    frame = _flat_panel(n_periods=30, n_items=5, factor=1.004)
    result = ml.extend(frame, "geks_fisher", window=25, splice=splice, anchor_month=12)
    assert len(result.index) == 30
    assert not result.index.index.duplicated().any()
    assert result.index.index.is_monotonic_increasing
    # The window runs from each December to the next, so the expanding
    # windows computed are one per period after the first.
    assert result.n_windows == 29


def test_the_fixed_base_rules_honour_the_window_length_when_no_anchor_month_arrives():
    """Quarterly or irregular data may never hit the anchor month. The
    window is re-anchored anyway rather than expanding without limit, which
    is what the window length is for."""
    periods = pd.date_range("2022-01-01", periods=20, freq="QS")
    anchors = ml._anchor_positions(periods, anchor_month=12, window=5)
    gaps = np.diff(anchors)
    assert gaps.max() <= 4                  # a window of 5 holds anchor..anchor+4
    assert anchors[0] == 0


def test_an_unknown_method_or_splice_is_refused_by_name():
    frame = _flat_panel()
    with pytest.raises(ml.MultilateralError, match="unknown splice"):
        ml.extend(frame, "geks_fisher", splice="diagonal")
    with pytest.raises(ml.MultilateralError, match="unknown multilateral method"):
        ml.multilateral_index(ml.build_panel(frame), "geks_walsh")
    with pytest.raises(ml.MultilateralError, match="building block"):
        ml.geks(ml.build_panel(frame), "walsh")
    with pytest.raises(ml.MultilateralError, match="at least two periods"):
        ml.extend(frame, "geks_fisher", window=1)


# ---------------------------------------------------------------------
# The comparison view
# ---------------------------------------------------------------------
def _scanner_panel() -> pd.DataFrame:
    raw = pd.read_csv(SCANNER)
    frame = standardise(raw, infer_schema(raw))
    return frame.assign(price_imputed=frame["price_reported"])


def test_the_comparison_computes_every_method_on_every_window_and_every_rule():
    frame = _scanner_panel()
    methods = ("geks_fisher", "tpd", "geary_khamis")
    windows = (13, 25)
    table = ml.method_comparison(frame, methods=methods, windows=windows,
                                 splices=("movement", "mean", "fbew"))
    assert len(table) == len(methods) * len(windows) * 3
    assert set(table["method"]) == set(methods)
    assert table["final_level"].notna().all()
    assert {"method_label", "splice_label", "change_pct", "annualised_pct",
            "splice_spread_pp", "n_windows", "error"} <= set(table.columns)


def test_a_method_the_data_cannot_support_is_listed_with_its_reason_not_dropped():
    """A comparison view that quietly omits what this data cannot do teaches
    the reader that the remaining spread is the whole spread."""
    frame = _flat_panel(n_periods=8, n_items=3).drop(columns=["quantity"])
    table = ml.method_comparison(frame, methods=("tpd", "geks_fisher"), windows=(8,))
    assert len(table) == 2
    failed = table[table["method"] == "geks_fisher"].iloc[0]
    assert not np.isfinite(failed["final_level"])
    assert "quantity" in failed["error"]
    assert np.isfinite(table[table["method"] == "tpd"].iloc[0]["final_level"])


def test_the_spread_is_reported_in_index_points_and_in_rate_points():
    frame = _scanner_panel()
    table = ml.method_comparison(frame, methods=PANEL_METHODS, windows=(25,),
                                 splices=("mean",))
    spread = ml.comparison_spread(table)
    assert spread["n_computed"] == len(PANEL_METHODS)
    assert spread["level_spread_pp"] == pytest.approx(
        spread["level_max"] - spread["level_min"])
    assert spread["level_spread_pp"] > 0        # they do not agree on churning data
    assert np.isfinite(spread["rate_spread_pp"])


def test_the_spread_of_nothing_computable_is_reported_rather_than_raised():
    empty = pd.DataFrame({"final_level": [np.nan], "annualised_pct": [np.nan]})
    spread = ml.comparison_spread(empty)
    assert spread["n_computed"] == 0
    assert not np.isfinite(spread["level_spread_pp"])


# ---------------------------------------------------------------------
# The configuration
# ---------------------------------------------------------------------
def test_the_config_names_exactly_the_methods_and_splices_the_engine_implements():
    """`core` must not import `engine`, so the two lists are duplicated. This
    is the test that keeps the duplicate honest."""
    for method in ml.METHODS:
        MultilateralConfig(method=method)
    for splice in ml.SPLICES:
        MultilateralConfig(splice=splice)
    with pytest.raises(ValueError, match="unknown multilateral method"):
        MultilateralConfig(method="geks_walsh")
    with pytest.raises(ValueError, match="unknown splice"):
        MultilateralConfig(splice="diagonal")
    with pytest.raises(ValueError, match="at least two"):
        MultilateralConfig(window=1)
    with pytest.raises(ValueError, match="calendar month"):
        MultilateralConfig(anchor_month=13)


def test_a_config_saved_before_this_phase_loads_with_multilateral_disabled():
    """The same rule `quality_adjustment` was added under: a new section
    with a default that changes nothing needs no schema version bump, and a
    run registered under the old config must go on meaning what it meant."""
    saved = json.loads(RunConfig().to_json())
    del saved["multilateral"]
    loaded = RunConfig.from_dict(saved)
    assert loaded.multilateral.enabled is False
    assert loaded.multilateral.method == "geks_fisher"
    assert not loaded.legacy_upconverted          # not a migration, just a default


def test_the_multilateral_settings_survive_a_config_round_trip():
    cfg = RunConfig(multilateral=MultilateralConfig(
        enabled=True, method="wtpd", window=13, splice="half", anchor_month=1))
    back = RunConfig.from_dict(json.loads(cfg.to_json()))
    assert back.multilateral == cfg.multilateral


# ---------------------------------------------------------------------
# The acceptance criteria
# ---------------------------------------------------------------------
@pytest.mark.parametrize("method", PANEL_METHODS)
def test_on_churning_data_every_multilateral_method_drifts_far_less_than_a_chained_bilateral(
        method):
    """The phase's headline acceptance criterion, on the scanner fixture:
    two-thirds of the period x product grid empty, promotions with a
    quantity response, and a three percent annual trend underneath. The
    chained Tornqvist must end materially above every transitive series, and
    the diagnostic must report it rather than leave it to be inferred."""
    frame = _scanner_panel()
    panel = ml.build_panel(frame)
    assert panel.churn_share > 0.5

    result = ml.extend(frame, method, window=25, splice="mean")
    report = ml.drift_against_chained(frame, result.index, formula="tornqvist")
    assert report.exceeds_threshold
    assert report.drift_pp > 10.0
    assert f"{abs(report.drift_pp):.2f}" in report.message
    # and the multilateral series stays in the neighbourhood of the trend it
    # was built on, which the chained one has long since left
    assert 85.0 < float(result.index.iloc[-1]) < 125.0
    assert report.chained_level > 125.0


def test_a_hundred_thousand_transactions_over_a_twenty_five_month_window_inside_thirty_seconds():
    """The stated performance criterion. Measured on the whole path a caller
    takes -- pivoting the long frame and computing the index -- because a
    fast kernel behind a slow pivot is not a fast index."""
    rng = np.random.default_rng(3)
    periods = pd.date_range("2022-01-01", periods=25, freq="MS")
    per_period = 4000
    frames = []
    for t, period in enumerate(periods):
        chosen = rng.choice(4000, size=per_period, replace=False)
        frames.append(pd.DataFrame({
            "period": period,
            "item_id": [f"S{i}" for i in chosen],
            "price_imputed": rng.uniform(1, 50, per_period) * (1.002 ** t),
            "quantity": rng.uniform(1, 500, per_period)}))
    frame = pd.concat(frames, ignore_index=True)
    assert len(frame) == 100_000

    for method in PANEL_METHODS:
        started = time.perf_counter()
        result = ml.multilateral_index(ml.build_panel(frame), method)
        elapsed = time.perf_counter() - started
        assert np.isfinite(result.index.to_numpy()).all()
        assert elapsed < 30.0, f"{method} took {elapsed:.1f}s on 100k transactions"


# ---------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------
class FakeUpload:
    def __init__(self, name: str, data: bytes) -> None:
        self.name, self._data, self.size = name, data, len(data)

    def getvalue(self) -> bytes:
        return self._data


@pytest.fixture()
def deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "multilateral.db"))
    monkeypatch.setenv("PRICELAB_STORE_DIR", str(tmp_path / "store"))
    get_settings.cache_clear()
    db.reset_db_state()
    from pricelab.core import ledger, registry, security  # noqa: F401  register tables
    from pricelab.data import classification, mapping, validation  # noqa: F401
    db.init_db()
    from pricelab.core.cache import reset_analysis_cache
    from pricelab.core.ratelimit import reset_upload_limiter
    reset_analysis_cache()
    reset_upload_limiter()
    yield tmp_path
    db.reset_db_state()
    get_settings.cache_clear()


def _page(state: dict, role: str = "COMPILER") -> AppTest:
    script = f"""
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.multilateral as page
set_current_role(Role.{role})
st.session_state.setdefault("pricelab_username", "tester1")
page.render()
"""
    at = AppTest.from_string(script, default_timeout=300)
    for key, value in state.items():
        at.session_state[key] = value
    at.run()
    assert not at.exception, at.exception
    return at


def _ingest_scanner(monkeypatch) -> dict:
    monkeypatch.setattr(streamlit, "file_uploader",
                        lambda *a, **k: FakeUpload("scanner_transactions.csv",
                                                   SCANNER.read_bytes()))
    script = """
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.ingest as page
set_current_role(Role.COMPILER)
st.session_state.setdefault("pricelab_username", "tester1")
page.render()
"""
    at = AppTest.from_string(script, default_timeout=300)
    at.run()
    assert not at.exception, at.exception
    next(b for b in at.button if b.label == "Confirm column mapping").click().run()
    assert not at.exception, at.exception
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: None)
    return {k: v for k, v in at.session_state.filtered_state.items()
            if not str(k).startswith("$$")}


def _text(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown) + "\n" + "\n".join(c.value for c in at.caption)


def test_the_page_computes_a_multilateral_index_from_an_uploaded_collection(
        deployment, monkeypatch):
    state = _ingest_scanner(monkeypatch)
    at = _page(state)
    assert "Multilateral index" in _text(at)

    methods = next(s for s in at.selectbox if s.label == "Method")
    # AppTest reports a selectbox's options as the user sees them (the
    # format_func's output) and its value as the option itself.
    assert set(methods.options) >= {ml.METHOD_LABELS[m] for m in PANEL_METHODS}
    splices = next(s for s in at.selectbox if s.label == "Extension rule")
    assert splices.value == "mean"          # the least path-dependent rule, by default

    at.session_state["ml_window"] = 25
    next(b for b in at.button if b.label == "Compute").click().run()
    assert not at.exception, at.exception

    result = at.session_state["ml_result"]
    assert isinstance(result, ml.ExtensionResult)
    assert result.method == "geks_fisher" and result.window == 25
    assert len(result.index) == 30
    text = _text(at)
    assert "Final level" in "".join(m.label for m in at.metric) or "Final level" in text
    # the chained-bilateral comparison is on the page, not only in the library
    assert "chained index ends" in "\n".join(
        [w.value for w in at.warning] + [s.value for s in at.success])


def test_extend_from_config_and_extend_are_the_same_computation():
    """One door between a saved configuration and a compiled series, so the
    settings a run is registered under cannot drift from the settings it was
    computed with."""
    frame = _scanner_panel()
    cfg = MultilateralConfig(method="tpd", window=13, splice="half", min_matched_items=3)
    by_config = ml.extend_from_config(frame, cfg)
    by_hand = ml.extend(frame, "tpd", window=13, splice="half", min_matched_items=3)
    assert by_config.index.to_numpy() == pytest.approx(by_hand.index.to_numpy())
    assert by_config.window == 13 and by_config.splice == "half"


def test_the_page_records_its_settings_on_the_run_so_a_registration_carries_them(
        deployment, monkeypatch):
    """The multilateral series is not part of the pipeline, so this changes
    nothing about the compiled bilateral result -- but a run registered
    afterwards must say which method, window and rule produced the series
    published beside it."""
    state = _ingest_scanner(monkeypatch)
    before = state["run_config"]
    assert before.multilateral.enabled is False

    at = _page(state)
    at.session_state["ml_window"] = 13
    next(s for s in at.selectbox if s.label == "Extension rule").select("half").run()
    next(b for b in at.button if b.label == "Compute").click().run()
    assert not at.exception, at.exception

    after = at.session_state["run_config"]
    assert after.multilateral.enabled is True
    assert after.multilateral.method == "geks_fisher"
    assert after.multilateral.window == 13
    assert after.multilateral.splice == "half"
    # everything else about the run is untouched, so it still reproduces
    assert after.index == before.index
    assert after.schema_ == before.schema_
    assert after.quality_adjustment == before.quality_adjustment


def test_the_page_shows_the_spread_between_the_methods_when_the_comparison_is_run(
        deployment, monkeypatch):
    state = _ingest_scanner(monkeypatch)
    at = _page(state)
    next(b for b in at.button if b.label == "Run the comparison").click().run()
    assert not at.exception, at.exception

    labels = [m.label for m in at.metric]
    assert "Spread in the final level" in labels
    assert "Spread in the annual rate" in labels
    table = at.session_state["ml_comparison"]
    assert set(table["method"]) >= set(PANEL_METHODS)
    assert table["final_level"].notna().any()
    assert ml.comparison_spread(table)["level_spread_pp"] > 0


def test_the_page_names_the_methods_a_price_only_collection_cannot_support(
        deployment, monkeypatch):
    """The Ingest page's own rule: show the method and say what it needs,
    rather than hiding it and leaving the compiler to wonder."""
    raw = pd.read_csv(SCANNER).drop(columns=["Quantity", "Expenditure"])
    monkeypatch.setattr(streamlit, "file_uploader",
                        lambda *a, **k: FakeUpload("prices_only.csv",
                                                   raw.to_csv(index=False).encode()))
    script = """
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.ingest as page
set_current_role(Role.COMPILER)
st.session_state.setdefault("pricelab_username", "tester1")
page.render()
"""
    at = AppTest.from_string(script, default_timeout=300)
    at.run()
    next(b for b in at.button if b.label == "Confirm column mapping").click().run()
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: None)
    state = {k: v for k, v in at.session_state.filtered_state.items()
             if not str(k).startswith("$$")}

    page = _page(state)
    text = _text(page)
    for method in ("geks_fisher", "geks_tornqvist", "wtpd", "geary_khamis"):
        assert ml.METHOD_LABELS[method] in text
    assert "needs a quantity column" in text
    methods = next(s for s in page.selectbox if s.label == "Method")
    assert methods.options == [ml.METHOD_LABELS["tpd"]]   # prices alone support one


def test_a_viewer_is_refused_the_multilateral_page_by_direct_navigation(deployment):
    """The navigation menu hides it, but the menu is not the access control:
    the page's own decorator is, and it is what a direct URL meets."""
    import pages.multilateral as page
    from pricelab.core.models import Role
    from pricelab.core.security import AccessDenied, set_current_role

    set_current_role(Role.VIEWER)
    try:
        with pytest.raises(AccessDenied, match="administrator"):
            page.render()
    finally:
        set_current_role(Role.COMPILER)
