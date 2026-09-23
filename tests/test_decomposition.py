"""Phase 7a, Task 1: rates, contributions, core measures, base effects,
diffusion and dispersion -- and the chart rule that keeps their units apart.

Real agency data
----------------
The contribution tests run on the euro-area HICP pulled through the Phase 2
Eurostat connector, replaying two responses recorded from genuine live calls
on 2026-09-23 (tests/fixtures/connectors/eurostat_hicp_ea_indices.json and
eurostat_hicp_ea_weights.json):

    GET .../sdmx/2.1/data/prc_hicp_midx/M.I15.<55 codes>.EA
        ?format=JSON&lang=en&startPeriod=2022-12&endPeriod=2025-12
    GET .../sdmx/2.1/data/prc_hicp_inw/A.<55 codes>.EA
        ?format=JSON&lang=en&startPeriod=2023&endPeriod=2025

The 55 codes are CP00, its twelve divisions and their groups
(`eurostat.HICP_CODES`); the responses are stored exactly as returned, apart
from whitespace. 2,035 index observations and 165 weights, none missing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import responses
from dbtarget import database_url
from matplotlib.figure import Figure
from streamlit.testing.v1 import AppTest

from pricelab.core import audit, db
from pricelab.core.config import get_settings
from pricelab.data.connectors.eurostat import (
    BASE_URL,
    HICP_CODES,
    HICP_INDEX_DATASET,
    HICP_WEIGHT_DATASET,
    EurostatConnector,
    hicp_key,
    hicp_tree,
)
from pricelab.engine import decomposition as dc
from pricelab.engine.aggregation import weighted_aggregate
from pricelab.reporting import charts

FIXTURES = Path(__file__).parent / "fixtures" / "connectors"
INDEX_FIXTURE = FIXTURES / "eurostat_hicp_ea_indices.json"
WEIGHT_FIXTURE = FIXTURES / "eurostat_hicp_ea_weights.json"


def _mock_eurostat(rsps: responses.RequestsMock) -> None:
    rsps.add(responses.GET, re.compile(rf"{re.escape(BASE_URL)}/{HICP_INDEX_DATASET}/.*"),
             body=INDEX_FIXTURE.read_text(encoding="utf-8"), status=200)
    rsps.add(responses.GET, re.compile(rf"{re.escape(BASE_URL)}/{HICP_WEIGHT_DATASET}/.*"),
             body=WEIGHT_FIXTURE.read_text(encoding="utf-8"), status=200)


@pytest.fixture(scope="module")
def hicp():
    """The HICP as the connector delivers it: fetched, validated, decoded."""
    connector = EurostatConnector(sleep_fn=lambda s: None, max_retries=1)
    with responses.RequestsMock() as rsps:
        _mock_eurostat(rsps)
        indices = connector.fetch(dataset=hicp_key(HICP_INDEX_DATASET, list(HICP_CODES), "EA"),
                                  startPeriod="2022-12", endPeriod="2025-12", use_cache=False)
        weights = connector.fetch(
            dataset=hicp_key(HICP_WEIGHT_DATASET, list(HICP_CODES), "EA", unit=None),
            startPeriod="2023", endPeriod="2025", use_cache=False)
    return indices, weights


@pytest.fixture(scope="module")
def tree(hicp):
    indices, weights = hicp
    return hicp_tree(indices.data, weights.data, 2025)


# ---------------------------------------------------------------------
# The connector and the tree
# ---------------------------------------------------------------------
def test_the_connector_keeps_each_observation_s_coicop_code(hicp):
    indices, weights = hicp
    assert "coicop" in indices.data.columns and "coicop" in weights.data.columns
    assert indices.data["coicop"].nunique() == 55
    assert len(indices.data) == 55 * 37
    assert indices.vintage.source == "eurostat"


def test_a_single_series_response_is_decoded_exactly_as_before():
    """The recorded single-series fixture has no varying dimension, so the
    new code column must not appear: existing callers see no change."""
    from pricelab.data.connectors._jsonstat import parse_jsonstat

    frame = parse_jsonstat(json.loads((FIXTURES / "eurostat_response.json").read_text()))
    assert list(frame.columns) == ["period", "value"]


def test_the_hicp_tree_has_three_levels_and_names_what_it_substituted(tree):
    depths = {code: len(code) for code in tree.leaf_indices.columns}
    assert set(depths.values()) == {4, 5}          # CP08 as a leaf, groups elsewhere
    assert "CP08" in tree.leaf_indices.columns
    assert any(note.startswith("CP08:") for note in tree.notes)
    assert tree.base_period == pd.Timestamp("2024-12-01")
    assert tree.leaf_indices.iloc[0].eq(100.0).all()


def test_the_re_aggregated_headline_reproduces_the_published_one(tree):
    """An independent check that the tree is the HICP's own arithmetic:
    rolling the 42 published groups up with the published weights gives the
    published all-items index to within the rounding of the published
    figures (one decimal on a level near 120)."""
    result = dc.tree_contributions(tree.leaf_indices, tree.leaf_weights, tree.parent_of,
                                   tree.base_period, pd.Timestamp("2025-12-01"),
                                   supplied_parent_weights=tree.supplied_parent_weights)
    assert result.indices is not None
    gap = (result.indices["CP00"] - tree.published["CP00"]).abs().max()
    assert gap < 0.01


# ---------------------------------------------------------------------
# Contributions: additive to eight decimal places at every level
# ---------------------------------------------------------------------
@pytest.mark.parametrize("end", pd.date_range("2025-01-01", "2025-12-01", freq="MS"))
def test_contributions_reconcile_to_eight_decimals_at_every_level_on_real_data(tree, end):
    result = dc.tree_contributions(tree.leaf_indices, tree.leaf_weights, tree.parent_of,
                                   tree.base_period, end,
                                   supplied_parent_weights=tree.supplied_parent_weights)
    assert result.reconciles, result.residual_pp
    table = result.table
    top = table[table["parent"] == "CP00"]
    assert round(top["contribution_to_headline_pp"].sum(), 8) == round(
        result.headline_change_pct, 8)
    # every parent, at every level, equals the sum of its children
    for parent in table.index[table.index.isin(table["parent"].dropna())]:
        kids = table[table["parent"] == parent]
        assert round(kids["contribution_to_headline_pp"].sum(), 8) == round(
            float(table.loc[parent, "contribution_to_headline_pp"]), 8), parent
        # and a child's contributions to its own parent sum to the parent's change
        if parent != "CP00":
            assert kids["contribution_to_parent_pp"].sum() == pytest.approx(
                float(table.loc[parent, "change_pct"]), abs=1e-9)


def test_the_weight_rounding_in_the_published_file_is_reported_not_absorbed(tree):
    result = dc.tree_contributions(tree.leaf_indices, tree.leaf_weights, tree.parent_of,
                                   tree.base_period, pd.Timestamp("2025-12-01"),
                                   supplied_parent_weights=tree.supplied_parent_weights)
    assert any(p.startswith("CP05: supplied weight 61.02") for p in result.problems)


@pytest.mark.parametrize("seed", range(5))
def test_contributions_reconcile_on_random_trees(seed):
    rng = np.random.default_rng(seed)
    periods = pd.date_range("2020-01-01", periods=15, freq="MS")
    parent_of: dict[str, str | None] = {"ALL": None}
    leaves: dict[str, pd.Series] = {}
    weights: dict[str, float] = {}
    for d in range(int(rng.integers(2, 6))):
        division = f"D{d}"
        parent_of[division] = "ALL"
        for g in range(int(rng.integers(1, 5))):
            leaf = f"{division}G{g}"
            parent_of[leaf] = division
            leaves[leaf] = pd.Series(100 * np.cumprod(1 + rng.normal(0.003, 0.02, 15)),
                                     index=periods)
            weights[leaf] = float(rng.uniform(0.5, 60))
    result = dc.tree_contributions(pd.DataFrame(leaves), weights, parent_of, periods[2],
                                   periods[-1])
    assert result.reconciles
    assert result.residual_pp < 1e-10


def test_a_tree_with_two_roots_is_refused():
    periods = pd.date_range("2020-01-01", periods=3, freq="MS")
    leaves = pd.DataFrame({"a": [100, 101, 102], "b": [100, 99, 98]}, index=periods, dtype=float)
    with pytest.raises(dc.DecompositionError, match="exactly one root"):
        dc.tree_contributions(leaves, {"a": 1, "b": 1}, {"a": None, "b": None},
                              periods[0], periods[-1])


def test_contributions_over_time_sum_to_the_aggregate_change_each_period(tree):
    top = pd.DataFrame({c: tree.leaf_indices[c] for c in tree.leaf_indices.columns})
    over = dc.contributions_over_time(top, tree.leaf_weights)
    aggregate = weighted_aggregate(top, tree.leaf_weights)
    change = (aggregate / aggregate.shift(1) - 1) * 100
    assert over.sum(axis=1).to_numpy() == pytest.approx(change.dropna().to_numpy(), abs=1e-10)


# ---------------------------------------------------------------------
# Rates
# ---------------------------------------------------------------------
def test_rates_by_hand():
    """A series rising 1% a month for 13 months, then flat for two.

    period on period at t=14: 0%. Year on year at t=12: 1.01^12 - 1 =
    12.6825%. Annualised at t=1: 1.01^12 - 1, the same. Three on three at
    t=5: mean(I3..I5) / mean(I0..I2) = 1.01^3 exactly, 3.0301%. Cumulative
    at t=14: 1.01^13 - 1 = 13.8093%."""
    values = [100 * 1.01 ** min(t, 13) for t in range(15)]
    periods = pd.date_range("2020-01-01", periods=15, freq="MS")
    table = dc.rates(pd.Series(values, index=periods))
    assert table["period_on_period_pct"].iloc[14] == pytest.approx(0.0)
    assert table["year_on_year_pct"].iloc[12] == pytest.approx(12.682503013196977)
    assert table["annualised_pct"].iloc[1] == pytest.approx(12.682503013196977)
    assert table["three_month_on_three_month_pct"].iloc[5] == pytest.approx(3.0301)
    assert table["cumulative_pct"].iloc[14] == pytest.approx((1.01 ** 13 - 1) * 100)
    assert np.isnan(table["year_on_year_pct"].iloc[11])


def test_rates_refuse_a_frequency_they_do_not_define():
    with pytest.raises(dc.DecompositionError, match="monthly"):
        dc.rates(pd.Series([1.0, 2.0]), periods_per_year=52)


# ---------------------------------------------------------------------
# Core measures on a distribution with known tails
# ---------------------------------------------------------------------
def _two_periods(changes: dict[str, float]) -> pd.DataFrame:
    """Every component at 100, then moved by its change: so the effective
    weights are the expenditure weights and the hand arithmetic is simple."""
    periods = pd.date_range("2024-01-01", periods=2, freq="MS")
    return pd.DataFrame({c: [100.0, 100.0 * (1 + x / 100)] for c, x in changes.items()},
                        index=periods)


TAILS = {"a": -10.0, "b": 1.0, "c": 2.0, "d": 3.0, "e": 20.0}


def test_trimmed_mean_by_hand():
    """Five components of equal weight 0.2, changes -10, 1, 2, 3, 20.

    20% from each tail removes -10 and 20 entirely: mean(1, 2, 3) = 2.
    10% from each tail removes half of each tail component's weight:
        (0.1 * -10 + 0.2 * 1 + 0.2 * 2 + 0.2 * 3 + 0.1 * 20) / 0.8
      = (-1 + 0.2 + 0.4 + 0.6 + 2) / 0.8 = 2.2 / 0.8 = 2.75
    0% is the weighted mean: 16 / 5 = 3.2, the aggregate's own change.
    """
    comps = _two_periods(TAILS)
    weights = dict.fromkeys(TAILS, 1.0)
    assert dc.trimmed_mean(comps, weights, trim_pct=20).series.iloc[1] == pytest.approx(2.0)
    assert dc.trimmed_mean(comps, weights, trim_pct=10).series.iloc[1] == pytest.approx(2.75)
    assert dc.trimmed_mean(comps, weights, trim_pct=0).series.iloc[1] == pytest.approx(3.2)


def test_weighted_median_by_hand():
    """Changes -10, 1, 2, 3, 20.

    Weights 0.1, 0.3, 0.2, 0.1, 0.3: cumulative 0.1, 0.4, 0.6 -- the half
    is crossed inside the component changing 2%, so the median is 2.
    Weights 0.1, 0.3, 0.1, 0.2, 0.3: cumulative 0.1, 0.4, 0.5 -- the half
    falls exactly on the boundary after 2%, so the median is the average of
    2 and 3, 2.5, the convention for an even split.
    """
    comps = _two_periods(TAILS)
    first = dict(zip(TAILS, (0.1, 0.3, 0.2, 0.1, 0.3), strict=True))
    second = dict(zip(TAILS, (0.1, 0.3, 0.1, 0.2, 0.3), strict=True))
    assert dc.weighted_median(comps, first).series.iloc[1] == pytest.approx(2.0)
    assert dc.weighted_median(comps, second).series.iloc[1] == pytest.approx(2.5)


def test_a_zero_trim_is_the_headline_on_real_data(tree):
    """Under effective weights the weighted mean of component changes is the
    aggregate's change exactly -- which is the property that makes the
    trimmed mean a trimmed version of the headline and not of something
    else."""
    comps, w = tree.leaf_indices, tree.leaf_weights
    aggregate = weighted_aggregate(comps, w)
    headline = (aggregate / aggregate.shift(1) - 1) * 100
    zero = dc.trimmed_mean(comps, w, trim_pct=0).series
    assert zero.dropna().to_numpy() == pytest.approx(headline.dropna().to_numpy(), abs=1e-10)


def test_every_core_measure_states_its_parameters_and_requirements(tree):
    comps, w = tree.leaf_indices, tree.leaf_weights
    measures = [
        dc.exclusion_measure(comps, w, ["CP01", "CP045"], parent_of=tree.parent_of),
        dc.trimmed_mean(comps, w, trim_pct=15),
        dc.weighted_median(comps, w),
        dc.variance_weighted(comps, w, window=6),
    ]
    for measure in measures:
        assert measure.parameters["horizon"] == 1
        assert measure.requirements == dc.CORE_REQUIREMENTS[measure.key]
        assert measure.name in measure.label
    assert "trim each tail pct 15" in measures[1].label
    # CP01 is a division: excluding it removes both its groups, and CP045
    # (electricity, gas and other fuels) is a leaf -- three components
    assert measures[0].parameters["excluded"] == ["CP01", "CP045"]
    assert measures[0].parameters["components_removed"] == 3
    kept = 1000 - 155.47 - tree.leaf_weights["CP045"]
    assert measures[0].parameters["basket_share_kept_pct"] == pytest.approx(
        kept / sum(w.values()) * 100, abs=0.01)


def test_availability_says_why_on_published_indices(tree):
    """The HICP within one year has 13 periods and no item-level prices:
    a year-on-year variance-weighted measure and a sticky-price measure
    cannot be computed, and the reasons say why."""
    reasons = dc.core_measure_availability(tree.leaf_indices, tree.leaf_weights, horizon=12,
                                           window=24)
    assert reasons["trimmed_mean"] is None and reasons["weighted_median"] is None
    assert "volatility window" in str(reasons["variance_weighted"])
    assert "item-level prices" in str(reasons["sticky_price"])
    assert "choose at least one" in str(reasons["exclusion"])


def test_availability_without_weights_refuses_everything_with_the_reason():
    comps = _two_periods(TAILS)
    reasons = dc.core_measure_availability(comps, None)
    assert all(r is not None and "no expenditure weights" in r for r in reasons.values())
    with pytest.raises(dc.DecompositionError, match="needs expenditure weights"):
        dc.trimmed_mean(comps, None)


def test_the_trim_must_leave_a_middle():
    with pytest.raises(dc.DecompositionError, match="below 50"):
        dc.trimmed_mean(_two_periods(TAILS), dict.fromkeys(TAILS, 1.0), trim_pct=50)


def test_variance_weighting_leans_on_the_quiet_component():
    """Two components, equal weights: one moves 0.2% +- 0.1 a month, the
    other 0.2% +- 3. The variance-weighted measure sits almost on the quiet
    one's change; the plain weighted mean sits halfway."""
    rng = np.random.default_rng(3)
    periods = pd.date_range("2020-01-01", periods=40, freq="MS")
    quiet = 100 * np.cumprod(1 + (0.2 + rng.normal(0, 0.1, 40)) / 100)
    loud = 100 * np.cumprod(1 + (0.2 + rng.normal(0, 3.0, 40)) / 100)
    comps = pd.DataFrame({"quiet": quiet, "loud": loud}, index=periods)
    measure = dc.variance_weighted(comps, {"quiet": 1.0, "loud": 1.0}, window=12).series
    quiet_change = (comps["quiet"] / comps["quiet"].shift(1) - 1) * 100
    gap = (measure - quiet_change).dropna().abs()
    assert gap.max() < 0.1
    assert measure.iloc[:13].isna().all()      # no estimate before a full window


def test_the_sticky_price_measure_classifies_by_frequency_of_change():
    """Bread's items reprice every month, Rent's every six: Rent is sticky
    at the 4.3-month threshold and Bread is not, and the measure is Rent's
    own index change."""
    periods = pd.date_range("2022-01-01", periods=24, freq="MS")
    rows = []
    for category, every in (("Bread", 1), ("Rent", 6)):
        for item in range(3):
            price = 10.0 + item
            for t, period in enumerate(periods):
                if t and t % every == 0:
                    price *= 1.01
                rows.append({"period": period, "category": category, "item_id": f"{category}{item}",
                             "price_clean": price})
    panel = pd.DataFrame(rows)
    comps = pd.DataFrame({c: panel[panel["category"] == c].groupby("period")["price_clean"].mean()
                          for c in ("Bread", "Rent")})
    comps = comps / comps.iloc[0] * 100
    measure = dc.sticky_price_measure(comps, {"Bread": 1.0, "Rent": 1.0}, panel)
    assert measure.parameters["sticky_components"] == ["Rent"]
    stats = measure.detail
    assert stats is not None
    assert stats.loc["Bread", "duration_months"] == pytest.approx(1.0)
    # Three changes (months 6, 12, 18) in 23 consecutive comparisons per item:
    # 23 / 3 months, not 6 -- the incomplete spells at either end of a finite
    # sample count as comparisons without a change, as they do in the
    # frequency approach the Atlanta Fed measure is built on.
    assert stats.loc["Rent", "duration_months"] == pytest.approx(23 / 3)
    rent = (comps["Rent"] / comps["Rent"].shift(1) - 1) * 100
    assert measure.series.dropna().to_numpy() == pytest.approx(rent.dropna().to_numpy())


def test_an_exclusion_measure_names_what_it_left_out():
    comps = _two_periods(TAILS)
    measure = dc.exclusion_measure(comps, dict.fromkeys(TAILS, 1.0), ["a", "e"])
    assert measure.series.iloc[1] == pytest.approx(2.0)        # mean of 1, 2, 3
    assert measure.parameters["excluded"] == ["a", "e"]
    with pytest.raises(dc.DecompositionError, match="no such component"):
        dc.exclusion_measure(comps, dict.fromkeys(TAILS, 1.0), ["zzz"])


# ---------------------------------------------------------------------
# Base effects: exact
# ---------------------------------------------------------------------
def test_base_effects_by_hand():
    """A year ago the index was 100; last December 102; now (March) 105.

    year on year = 5%; carry-over = (102 - 100) / 100 = 2 pp; impulse =
    (105 - 102) / 100 = 3 pp: 2 + 3 = 5, exactly."""
    periods = pd.date_range("2023-03-01", periods=13, freq="MS")
    values = np.interp(np.arange(13), [0, 9, 12], [100.0, 102.0, 105.0])
    effects = dc.base_effects(pd.Series(values, index=periods)).frame
    march = effects.loc["2024-03-01"]
    assert march["year_on_year_pct"] == pytest.approx(5.0)
    assert march["carry_over_pp"] == pytest.approx(2.0)
    assert march["impulse_pp"] == pytest.approx(3.0)


def test_base_effects_sum_to_the_year_on_year_rate_exactly_on_real_data(hicp):
    indices, _ = hicp
    data = indices.data
    headline = data[data["coicop"] == "CP00"].set_index("period")["value"].astype(float)
    frame = dc.base_effects(headline).frame.dropna()
    assert len(frame) >= 12
    assert (frame["carry_over_pp"] + frame["impulse_pp"]).to_numpy() == pytest.approx(
        frame["year_on_year_pct"].to_numpy(), abs=1e-12)
    assert (frame["this_period_pp"] - frame["base_effect_pp"]).to_numpy() == pytest.approx(
        frame["change_in_yoy_pp"].to_numpy(), abs=1e-12)
    # December carries nothing over: the base and "last December" coincide
    december = frame[frame.index.month == 12]
    assert december["carry_over_pp"].abs().max() < 1e-12


def test_base_effects_refuse_a_series_with_gaps():
    periods = pd.DatetimeIndex(["2020-01-01", "2020-02-01", "2020-04-01"])
    with pytest.raises(dc.DecompositionError, match="regular series"):
        dc.base_effects(pd.Series([1.0, 2.0, 3.0], index=periods))


# ---------------------------------------------------------------------
# Diffusion and dispersion
# ---------------------------------------------------------------------
def test_diffusion_by_hand():
    """Changes -10, 1, 2, 3, 0 (one unchanged): 3 of 5 rising (60%), 1
    unchanged, 1 falling; diffusion index 60 + 20/2 = 70. With equal
    weights, the share of the basket rising faster than 2.5% is 20% (d)."""
    changes = {"a": -10.0, "b": 1.0, "c": 2.0, "d": 3.0, "e": 0.0}
    out = dc.diffusion(_two_periods(changes), dict.fromkeys(changes, 1.0), threshold_pct=2.5)
    row = out.iloc[1]
    assert row["share_rising_pct"] == pytest.approx(60.0)
    assert row["share_unchanged_pct"] == pytest.approx(20.0)
    assert row["diffusion_index"] == pytest.approx(70.0)
    assert row["weighted_share_above_threshold_pct"] == pytest.approx(20.0)
    assert out.iloc[0].isna().all()


def test_dispersion_and_skew_by_hand():
    """Changes -1, 0, 1, 4 at equal weight: mean 1, deviations -2, -1, 0, 3,
    variance (4 + 1 + 0 + 9) / 4 = 3.5, third moment (-8 - 1 + 0 + 27) / 4 =
    4.5, skewness 4.5 / 3.5^1.5 = 0.6872 -- positive, a long upper tail."""
    changes = {"a": -1.0, "b": 0.0, "c": 1.0, "d": 4.0}
    row = dc.dispersion(_two_periods(changes), dict.fromkeys(changes, 1.0)).iloc[1]
    assert row["mean_pct"] == pytest.approx(1.0)
    assert row["dispersion_pp"] == pytest.approx(np.sqrt(3.5))
    assert row["skewness"] == pytest.approx(4.5 / 3.5 ** 1.5)


# ---------------------------------------------------------------------
# The chart rule: no mixed units on an axis
# ---------------------------------------------------------------------
def _axis() -> tuple[Figure, object]:
    fig = Figure()
    return fig, fig.add_subplot(111)


def test_an_axis_mixing_an_index_level_and_a_percentage_change_is_refused():
    fig, ax = _axis()
    charts.mark(ax.plot([1, 2], [100, 101], label="level"), "index_level")
    charts.mark(ax.plot([1, 2], [0.5, 1.0], label="rate"), "percent_change")
    with pytest.raises(charts.ChartUnitError, match="mixes 2 units"):
        charts.check_figure(fig)


def test_an_axis_mixing_a_percentage_change_and_a_contribution_is_refused():
    fig, ax = _axis()
    charts.mark(ax.bar([1, 2], [0.2, 0.3], label="food"), "percentage_points")
    charts.mark(ax.plot([1, 2], [0.5, 0.6], label="headline, %"), "percent_change")
    with pytest.raises(charts.ChartUnitError, match="percentage-point contribution"):
        charts.check_figure(fig)


def test_nominal_and_real_on_one_axis_need_labels_that_say_so():
    fig, ax = _axis()
    charts.mark(ax.plot([1, 2], [100, 110], label="earnings"), "currency", "nominal")
    charts.mark(ax.plot([1, 2], [100, 104], label="earnings, real"), "currency", "real")
    ax.set_ylabel("pounds")
    with pytest.raises(charts.ChartUnitError, match="does not say it is nominal"):
        charts.check_figure(fig)

    fig, ax = _axis()
    charts.mark(ax.plot([1, 2], [100, 110], label="earnings, nominal"), "currency", "nominal")
    charts.mark(ax.plot([1, 2], [100, 104], label="earnings, real"), "currency", "real")
    with pytest.raises(charts.ChartUnitError, match="no axis label"):
        charts.check_figure(fig)
    ax.set_ylabel("pounds: nominal in current prices, real in constant 2020 prices")
    charts.check_figure(fig)


def test_an_unmarked_artist_is_a_failure_not_an_exemption():
    fig, ax = _axis()
    ax.plot([1, 2], [1, 2])
    with pytest.raises(charts.ChartUnitError, match="unmarked"):
        charts.check_figure(fig)


def test_every_decomposition_and_deflation_chart_keeps_its_units_apart(tree):
    from pricelab.engine import deflation as dfl

    over = dc.contributions_over_time(tree.leaf_indices.iloc[:, :6],
                                      {c: tree.leaf_weights[c]
                                       for c in tree.leaf_indices.columns[:6]})
    rates = dc.rates(tree.published["CP00"])
    periods = pd.date_range("2020-01-01", periods=24, freq="MS")
    deflated = dfl.deflate(pd.Series(np.linspace(1000, 1200, 24), index=periods),
                           pd.Series(np.linspace(100, 110, 24), index=periods),
                           reference=periods[0], nominal_name="earnings", deflator_name="CPI")
    for fig in (charts.contributions_chart(over), charts.rates_chart(
            {"pop": rates["period_on_period_pct"]}, "rates"), charts.deflation_chart(deflated)):
        charts.check_figure(fig)
        assert all(getattr(a, "_pricelab_unit", None) for ax in fig.axes
                   for a in [*ax.lines, *ax.patches, *ax.collections])


# ---------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------
@pytest.fixture()
def deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICELAB_DATABASE_URL", database_url(tmp_path, "decomposition.db"))
    monkeypatch.setenv("PRICELAB_STORE_DIR", str(tmp_path / "store"))
    get_settings.cache_clear()
    db.reset_db_state()
    from pricelab.core import ledger, registry, security  # noqa: F401  register tables
    from pricelab.data import classification, mapping, validation  # noqa: F401
    db.init_db()
    import pages.sources as sources

    sources._cache = None          # no response left over from another test
    yield tmp_path
    db.reset_db_state()
    get_settings.cache_clear()


def _page(state: dict | None = None, role: str = "ANALYST") -> AppTest:
    script = f"""
import streamlit as st
from pricelab.core.models import Role
from pricelab.core.security import set_current_role
import pages.decomposition as page
set_current_role(Role.{role})
st.session_state.setdefault("pricelab_username", "analyst1")
page.render()
"""
    at = AppTest.from_string(script, default_timeout=120)
    for key, value in (state or {}).items():
        at.session_state[key] = value
    at.run()
    assert not at.exception, at.exception
    return at


def test_the_page_decomposes_the_published_hicp_and_shows_it_reconciles(deployment):
    at = _page({"dc_source": "Eurostat HICP (official, with item weights)"})
    with responses.RequestsMock() as rsps:
        _mock_eurostat(rsps)
        next(b for b in at.button if b.label == "Fetch from Eurostat").click().run()
    assert not at.exception, at.exception

    result = at.session_state["dc_contributions"]
    assert result.root == "CP00" and result.reconciles
    labels = [m.label for m in at.metric]
    assert "Largest reconciliation gap, any level" in labels
    assert "Year on year" in labels and "Diffusion index" in labels
    assert any("CP08:" in i.value for i in at.info)       # the substitution, stated

    # the fetch went through the connector and was audited
    with db.session_scope() as s:
        actions = [e.action for e in s.query(audit.AuditEventORM).all()]
    assert audit.EXTERNAL_FETCH_SUCCESS in actions and audit.DECOMPOSITION in actions

    # core measures: the ones this data supports are computed, the rest say why
    text = "\n".join(m.value for m in at.markdown)
    assert "Sticky price** needs item-level prices" in text
    assert "Not available here: no item-level prices" in text
    next(b for b in at.button if b.label == "Compute core measures").click().run()
    assert not at.exception, at.exception
    computed = at.session_state["dc_core"]
    assert {"trimmed_mean", "weighted_median"} <= set(computed)
    assert "sticky_price" not in computed
    assert computed["trimmed_mean"].parameters["trim_each_tail_pct"] == 15.0


def test_the_page_on_a_run_without_weights_says_why_it_cannot_decompose(deployment):
    """Two categories on an equally weighted geometric headline: rates and
    base effects still work, contributions are refused with the reason."""
    from pricelab import infer_schema, run_pipeline, standardise
    from pricelab.core.config import RunConfig

    rows = []
    for t, period in enumerate(pd.date_range("2022-01-01", periods=26, freq="MS")):
        for c, drift in (("Bread", 0.004), ("Milk", 0.002)):
            for i in range(3):
                rows.append({"Date": period, "Category": c, "Item_ID": f"{c}{i}",
                             "Item_Name": f"{c} {i}",
                             "Reported_Price": round((5 + i) * (1 + drift) ** t, 4)})
    raw = pd.DataFrame(rows)
    res = run_pipeline(standardise(raw, infer_schema(raw)), RunConfig())
    at = _page({"analysis": {"result": res, "narrative": None, "decisions": [],
                             "label": "unweighted"}})
    warnings = [w.value for w in at.warning]
    assert any("need expenditure weights" in w and "geometric mean" in w for w in warnings)
    assert "Year on year" in [m.label for m in at.metric]
    assert "dc_contributions" not in at.session_state


def test_the_page_decomposes_a_weighted_run_with_its_sticky_prices(deployment):
    from pricelab import infer_schema, run_pipeline, standardise
    from pricelab.core.config import RunConfig

    rng = np.random.default_rng(0)
    rows = []
    periods = pd.date_range("2022-01-01", periods=30, freq="MS")
    for category, (weight, every) in {"Bread": (40, 1), "Fuel": (25, 1), "Rent": (35, 6)}.items():
        for i in range(4):
            price = 10.0 + i
            for t, period in enumerate(periods):
                if t and t % every == 0:
                    price *= 1 + rng.normal(0.004, 0.01)
                rows.append({"Date": period, "Category": category, "Item_ID": f"{category}{i}",
                             "Item_Name": f"{category} {i}", "Reported_Price": round(price, 4),
                             "Weight": weight / 4})
    raw = pd.DataFrame(rows)
    res = run_pipeline(standardise(raw, infer_schema(raw)), RunConfig())
    at = _page({"analysis": {"result": res, "narrative": None, "decisions": [],
                             "label": "weighted"}, "dc_exclude": ["Fuel"]})
    result = at.session_state["dc_contributions"]
    assert result.reconciles
    assert result.headline_change_pct == pytest.approx(
        (res["indices"]["All items"].iloc[-1] / res["indices"]["All items"].iloc[0] - 1) * 100)
    next(b for b in at.button if b.label == "Compute core measures").click().run()
    assert not at.exception, at.exception
    computed = at.session_state["dc_core"]
    assert set(computed) == set(dc.CORE_MEASURES)
    assert computed["sticky_price"].parameters["sticky_components"] == ["Rent"]
    assert computed["exclusion"].parameters["excluded"] == ["Fuel"]


def test_a_viewer_is_refused_the_decomposition_page(deployment):
    import pages.decomposition as page
    from pricelab.core.models import Role
    from pricelab.core.security import AccessDenied, set_current_role

    set_current_role(Role.VIEWER)
    try:
        with pytest.raises(AccessDenied):
            page.render()
    finally:
        set_current_role(Role.COMPILER)
