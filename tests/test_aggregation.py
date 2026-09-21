"""Weighted roll-up through a classification tree, and the weight
problems it reports rather than absorbs.

The additivity property itself is proved on random trees in
tests/test_axioms.py. This file covers the behaviours that property
cannot: what happens to a category the weights file forgot, to a parent
whose stated weight disagrees with its children, and to the equally
weighted aggregate that every existing run still uses.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pricelab.engine import aggregation as agg


def _leaves():
    periods = pd.date_range("2020-01-01", periods=3, freq="MS")
    indices = pd.DataFrame({
        "01.1.1": [100.0, 110.0, 120.0],
        "01.1.2": [100.0, 100.0, 100.0],
        "01.2.1": [100.0, 90.0, 80.0],
    }, index=periods)
    parent_of = {
        "01": None, "01.1": "01", "01.2": "01",
        "01.1.1": "01.1", "01.1.2": "01.1", "01.2.1": "01.2",
    }
    weights = {"01.1.1": 50.0, "01.1.2": 30.0, "01.2.1": 20.0}
    return indices, weights, parent_of, periods


# ---------------------------------------------------------------------
# The migrated equally weighted aggregate
# ---------------------------------------------------------------------
def test_the_equally_weighted_aggregate_is_the_geometric_mean_of_the_columns():
    """Migrated out of build_all unchanged. Every collection with no
    expenditure weights still gets exactly this."""
    I = pd.DataFrame({"a": [100.0, 110.0], "b": [100.0, 90.0]})
    out = agg.equally_weighted_aggregate(I)
    assert out.tolist() == pytest.approx([100.0, float(np.sqrt(110.0 * 90.0))])


def test_build_all_still_produces_the_migrated_aggregate():
    """The migration is only safe if build_all's output is unchanged, so
    this asserts the two are the same object of arithmetic rather than
    trusting that they are."""
    from pricelab.core.config import IndexConfig
    from pricelab.engine.index import build_all

    periods = pd.date_range("2020-01-01", periods=3, freq="MS")
    df = pd.DataFrame({
        "period": list(periods) * 2,
        "category": ["Bread"] * 3 + ["Milk"] * 3,
        "item_id": ["1"] * 3 + ["2"] * 3,
        "price_imputed": [1.0, 1.1, 1.2, 2.0, 1.9, 2.2],
    })
    I, _matched = build_all(df, IndexConfig())
    expected = agg.equally_weighted_aggregate(I[["Bread", "Milk"]])
    pd.testing.assert_series_equal(I["All items"], expected, check_names=False)


# ---------------------------------------------------------------------
# Weighted aggregation
# ---------------------------------------------------------------------
def test_the_weighted_aggregate_is_the_weighted_arithmetic_mean():
    indices, weights, _parent_of, _periods = _leaves()
    out = agg.weighted_aggregate(indices, weights)
    expected = (110.0 * 50 + 100.0 * 30 + 90.0 * 20) / 100
    assert out.iloc[1] == pytest.approx(expected)


def test_a_category_the_weights_file_forgot_is_dropped_not_defaulted():
    """Silently assigning a missing category a weight of zero, or of the
    mean, would bury a gap in the weights file inside a plausible
    number."""
    indices, weights, _parent_of, _periods = _leaves()
    partial = {k: v for k, v in weights.items() if k != "01.2.1"}
    out = agg.weighted_aggregate(indices, partial)
    expected = (110.0 * 50 + 100.0 * 30) / 80
    assert out.iloc[1] == pytest.approx(expected)


def test_no_usable_weights_gives_nan_rather_than_an_exception():
    indices, _weights, _parent_of, _periods = _leaves()
    assert agg.weighted_aggregate(indices, {}).isna().all()
    assert agg.weighted_aggregate(indices, {"01.1.1": 0.0}).isna().all()


# ---------------------------------------------------------------------
# Roll-up
# ---------------------------------------------------------------------
def test_the_tree_rolls_up_and_derives_each_parents_weight():
    indices, weights, parent_of, _periods = _leaves()
    result = agg.aggregate_tree(indices, weights, parent_of)

    assert result.weights["01.1"] == pytest.approx(80.0)
    assert result.weights["01.2"] == pytest.approx(20.0)
    assert result.weights["01"] == pytest.approx(100.0)
    assert result.indices["01.1"].iloc[1] == pytest.approx((110.0 * 50 + 100.0 * 30) / 80)
    assert result.indices["01"].iloc[1] == pytest.approx(
        (110.0 * 50 + 100.0 * 30 + 90.0 * 20) / 100)
    assert result.problems == []


def test_a_supplied_parent_weight_that_disagrees_with_its_children_is_reported():
    """Reported, and the children's sum used. An aggregate that is not
    the sum of its parts cannot decompose into contributions that add
    up, so the alternative would break additivity silently."""
    indices, weights, parent_of, _periods = _leaves()
    result = agg.aggregate_tree(indices, weights, parent_of,
                                supplied_parent_weights={"01.1": 95.0})

    assert any("01.1" in p for p in result.problems)
    assert result.weights["01.1"] == pytest.approx(80.0)


def test_a_supplied_parent_weight_that_agrees_is_not_reported():
    indices, weights, parent_of, _periods = _leaves()
    result = agg.aggregate_tree(indices, weights, parent_of,
                                supplied_parent_weights={"01.1": 80.0})
    assert result.problems == []


def test_a_negative_weight_is_refused_outright():
    """A negative expenditure weight has no interpretation in a
    consumption basket, and it would silently invert that item's
    contribution to the headline."""
    indices, weights, parent_of, _periods = _leaves()
    weights = {**weights, "01.2.1": -20.0}
    with pytest.raises(agg.WeightError, match="negative weights"):
        agg.aggregate_tree(indices, weights, parent_of)


def test_weight_hierarchy_validation_is_the_same_rule_the_ingestion_layer_uses():
    """One definition of the weight-sum rule, reused rather than
    reimplemented -- a second copy could drift from the one Phase 2
    validates uploaded weights against."""
    from pricelab.data.classification import validate_weight_hierarchy

    assert agg.validate_weight_hierarchy is validate_weight_hierarchy


# ---------------------------------------------------------------------
# Contributions
# ---------------------------------------------------------------------
def test_contributions_sum_to_the_headline_change():
    indices, weights, parent_of, periods = _leaves()
    result = agg.aggregate_tree(indices, weights, parent_of)
    contrib = agg.contributions(indices, weights, periods[0], periods[-1])

    root = result.indices["01"]
    headline = (root.iloc[-1] / root.iloc[0] - 1.0) * 100.0
    assert contrib.sum() == pytest.approx(headline, abs=1e-10)


def test_a_heavily_weighted_category_that_barely_moves_can_outweigh_a_light_one_that_soars():
    """The reason a contribution and a percentage change are different
    columns in the table below: they answer different questions and can
    point in different directions."""
    periods = pd.date_range("2020-01-01", periods=2, freq="MS")
    indices = pd.DataFrame({"big": [100.0, 102.0], "small": [100.0, 130.0]}, index=periods)
    contrib = agg.contributions(indices, {"big": 95.0, "small": 5.0},
                                periods[0], periods[-1])

    change_pct = (indices.iloc[-1] / indices.iloc[0] - 1.0) * 100.0

    # "small" moved fifteen times as far in percentage terms...
    assert change_pct["small"] > change_pct["big"] * 10
    # ...and still contributed less to the headline, because of its weight.
    assert contrib["big"] == pytest.approx(95.0 * 2.0 / 100.0)
    assert contrib["small"] == pytest.approx(5.0 * 30.0 / 100.0)
    assert contrib["big"] > contrib["small"]


def test_contributions_are_empty_when_no_column_has_a_weight():
    indices, _weights, _parent_of, periods = _leaves()
    assert agg.contributions(indices, {}, periods[0], periods[-1]).empty


def test_contributions_are_nan_when_the_base_level_is_unusable():
    periods = pd.date_range("2020-01-01", periods=2, freq="MS")
    indices = pd.DataFrame({"a": [0.0, 100.0]}, index=periods)
    contrib = agg.contributions(indices, {"a": 1.0}, periods[0], periods[-1])
    assert contrib.isna().all()


def test_the_contribution_table_carries_the_pieces_a_reader_needs_to_check_it():
    indices, weights, parent_of, periods = _leaves()
    result = agg.aggregate_tree(indices, weights, parent_of)
    table = agg.contribution_table(result, periods[0], periods[-1],
                                   nodes=list(weights))

    assert list(table.columns) == [
        "weight", "level_start", "level_end", "change_pct", "contribution_pp"]
    assert table.loc["01.1.1", "change_pct"] == pytest.approx(20.0)
    assert table.loc["01.2.1", "change_pct"] == pytest.approx(-20.0)
    assert table["contribution_pp"].sum() == pytest.approx(
        (result.indices["01"].iloc[-1] / result.indices["01"].iloc[0] - 1) * 100, abs=1e-10)


def test_the_contribution_table_defaults_to_every_weighted_node():
    indices, weights, parent_of, periods = _leaves()
    result = agg.aggregate_tree(indices, weights, parent_of)
    table = agg.contribution_table(result, periods[0], periods[-1])
    assert set(weights) <= set(table.index)


def test_a_leaf_with_an_index_but_no_weight_is_reported_not_silently_dropped():
    """`weighted_aggregate` is right to exclude it (a forgotten weight is
    not a weight of zero), but an aggregate computed without one of its
    leaves is indistinguishable from one computed with it, so the
    exclusion has to be stated in `problems`."""
    indices, weights, parent_of, _periods = _leaves()
    weights = {k: v for k, v in weights.items() if k != "01.2.1"}
    result = agg.aggregate_tree(indices, weights, parent_of)

    assert "01.2.1" not in result.weights.index
    assert "01.2" not in result.indices.columns          # its only child had no weight
    assert result.indices["01"].iloc[1] == pytest.approx((110.0 * 50 + 100.0 * 30) / 80)
    assert any(p.startswith("01.2.1: has an index but no finite weight") for p in result.problems)
