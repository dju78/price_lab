"""Bilateral aggregate indices, with the weight-reference-period pair --
Lowe and Young -- and the price updating that separates them.

The axioms these formulae satisfy are in tests/test_axioms.py. This file
covers what those cannot: the specific arithmetic of each formula, the
identity that links Lowe to Young, and the degenerate inputs an engine
meets in practice.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pricelab.engine import bilateral as bi

ITEMS = ["bread", "milk", "eggs"]


def _panel():
    p0 = pd.Series([1.00, 2.00, 3.00], index=ITEMS)
    pt = pd.Series([1.20, 1.90, 4.00], index=ITEMS)
    q0 = pd.Series([10.0, 5.0, 2.0], index=ITEMS)
    qt = pd.Series([8.0, 7.0, 1.5], index=ITEMS)
    return p0, pt, q0, qt


# ---------------------------------------------------------------------
# The arithmetic of each formula, against hand-computed values
# ---------------------------------------------------------------------
def test_laspeyres_prices_the_base_basket():
    p0, pt, q0, _qt = _panel()
    expected = (1.20 * 10 + 1.90 * 5 + 4.00 * 2) / (1.00 * 10 + 2.00 * 5 + 3.00 * 2)
    assert bi.laspeyres(p0, pt, q0).value == pytest.approx(expected)


def test_paasche_prices_the_current_basket():
    p0, pt, _q0, qt = _panel()
    expected = (1.20 * 8 + 1.90 * 7 + 4.00 * 1.5) / (1.00 * 8 + 2.00 * 7 + 3.00 * 1.5)
    assert bi.paasche(p0, pt, qt).value == pytest.approx(expected)


def test_fisher_is_the_geometric_mean_of_its_two_legs_and_reports_them():
    p0, pt, q0, qt = _panel()
    result = bi.fisher(p0, pt, q0, qt)
    laspeyres = bi.laspeyres(p0, pt, q0).value
    paasche = bi.paasche(p0, pt, qt).value

    assert result.value == pytest.approx(np.sqrt(laspeyres * paasche))
    assert result.components["laspeyres"] == pytest.approx(laspeyres)
    assert result.components["paasche"] == pytest.approx(paasche)
    assert result.components["substitution_bias_pp"] == pytest.approx(
        bi.substitution_bias_pp(laspeyres, paasche))


def test_tornqvist_is_the_geometric_mean_of_the_geometric_laspeyres_and_paasche():
    """Not an arbitrary identity: it is why Tornqvist is the geometric
    family's counterpart to Fisher, and it pins the share weighting."""
    p0, pt, q0, qt = _panel()
    assert bi.tornqvist(p0, pt, q0, qt).value == pytest.approx(
        np.sqrt(bi.geometric_laspeyres(p0, pt, q0).value
                * bi.geometric_paasche(p0, pt, qt).value))


def test_walsh_prices_the_geometric_mean_basket():
    p0, pt, q0, qt = _panel()
    basket = np.sqrt(q0.to_numpy() * qt.to_numpy())
    expected = (pt.to_numpy() * basket).sum() / (p0.to_numpy() * basket).sum()
    assert bi.walsh(p0, pt, q0, qt).value == pytest.approx(expected)


def test_marshall_edgeworth_prices_the_arithmetic_mean_basket():
    p0, pt, q0, qt = _panel()
    basket = (q0.to_numpy() + qt.to_numpy()) / 2.0
    expected = (pt.to_numpy() * basket).sum() / (p0.to_numpy() * basket).sum()
    assert bi.marshall_edgeworth(p0, pt, q0, qt).value == pytest.approx(expected)


def test_every_symmetric_average_sits_between_laspeyres_and_paasche():
    p0, pt, q0, qt = _panel()
    low = min(bi.laspeyres(p0, pt, q0).value, bi.paasche(p0, pt, qt).value)
    high = max(bi.laspeyres(p0, pt, q0).value, bi.paasche(p0, pt, qt).value)
    for result in (bi.fisher(p0, pt, q0, qt), bi.tornqvist(p0, pt, q0, qt),
                   bi.walsh(p0, pt, q0, qt), bi.marshall_edgeworth(p0, pt, q0, qt)):
        assert low - 1e-12 <= result.value <= high + 1e-12, result.formula


# ---------------------------------------------------------------------
# Matching and sample size
# ---------------------------------------------------------------------
def test_an_item_priced_in_only_one_period_is_excluded_from_every_formula():
    """Item replacement must not register as price change, the rule the
    whole engine is built on -- it applies to the weighted formulae as
    much as the elementary ones."""
    p0, pt, q0, qt = _panel()
    pt = pt.copy()
    pt.loc["eggs"] = np.nan

    for result in (bi.laspeyres(p0, pt, q0), bi.paasche(p0, pt, qt),
                   bi.fisher(p0, pt, q0, qt), bi.tornqvist(p0, pt, q0, qt),
                   bi.walsh(p0, pt, q0, qt)):
        assert result.n_items == 2, result.formula


def test_imputed_values_are_counted_separately_from_the_sample_size():
    p0, pt, q0, _qt = _panel()
    imputed = pd.Series([False, True, False], index=ITEMS)
    result = bi.laspeyres(p0, pt, q0, imputed)
    assert result.n_items == 3
    assert result.n_imputed == 1
    assert result.imputed_share == pytest.approx(1 / 3)


def test_no_matched_items_gives_nan_rather_than_an_exception():
    empty = pd.Series(dtype=float)
    for result in (bi.laspeyres(empty, empty, empty), bi.fisher(empty, empty, empty, empty),
                   bi.tornqvist(empty, empty, empty, empty), bi.walsh(empty, empty, empty, empty),
                   bi.marshall_edgeworth(empty, empty, empty, empty),
                   bi.lowe(empty, empty, empty), bi.young(empty, empty, empty),
                   bi.geometric_laspeyres(empty, empty, empty),
                   bi.geometric_paasche(empty, empty, empty),
                   bi.fisher_quantity(empty, empty, empty, empty)):
        assert np.isnan(result.value), result.formula
        assert result.n_items == 0
    assert np.isnan(bi.value_ratio(empty, empty, empty, empty))


def test_zero_quantities_give_nan_rather_than_a_division_by_zero():
    p0, pt, _q0, _qt = _panel()
    zero = pd.Series([0.0, 0.0, 0.0], index=ITEMS)
    assert np.isnan(bi.laspeyres(p0, pt, zero).value)
    assert np.isnan(bi.paasche(p0, pt, zero).value)
    assert np.isnan(bi.young(p0, pt, zero).value)
    assert np.isnan(bi.walsh(p0, pt, zero, zero).value)
    assert np.isnan(bi.tornqvist(p0, pt, zero, zero).value)
    assert np.isnan(bi.geometric_laspeyres(p0, pt, zero).value)
    assert np.isnan(bi.geometric_paasche(p0, pt, zero).value)
    assert np.isnan(bi.marshall_edgeworth(p0, pt, zero, zero).value)
    assert np.isnan(bi.fisher_quantity(p0, pt, zero, zero).value)


# ---------------------------------------------------------------------
# Lowe, Young, and price updating -- the phase's acceptance criterion
# ---------------------------------------------------------------------
def _weight_reference_panel():
    """Prices at the weight reference period b, the price reference
    period 0, and the current period t, plus b's expenditure shares."""
    pb = pd.Series([0.80, 2.20, 2.50], index=ITEMS)
    p0 = pd.Series([1.00, 2.00, 3.00], index=ITEMS)
    pt = pd.Series([1.20, 1.90, 4.00], index=ITEMS)
    sb = pd.Series([0.50, 0.30, 0.20], index=ITEMS)
    return pb, p0, pt, sb


def test_a_lowe_and_a_young_index_on_the_same_data_differ():
    """The acceptance criterion, stated directly: the two formulae give
    different answers on the same collection, because holding quantities
    fixed and holding expenditure shares fixed are different assumptions
    once prices have moved between b and 0."""
    pb, p0, pt, sb = _weight_reference_panel()
    report = bi.price_updating_effect(p0, pt, pb, sb)

    assert report.lowe != pytest.approx(report.young)
    assert report.difference_pp != pytest.approx(0.0)


def test_the_difference_is_attributed_to_price_updating_in_the_output():
    """And the attribution is in the returned object, not left for the
    reader to subtract: `difference_pp` is the effect of price updating,
    and `weight_price_change` is the price movement between b and 0 that
    drives it."""
    pb, p0, pt, sb = _weight_reference_panel()
    report = bi.price_updating_effect(p0, pt, pb, sb)

    assert report.difference_pp == pytest.approx((report.lowe - report.young) * 100.0)
    assert report.weight_price_change == pytest.approx(
        float((sb * (p0 / pb)).sum() / sb.sum()))
    assert report.updated_shares.sum() == pytest.approx(1.0)


def test_lowe_equals_young_when_no_price_moved_between_the_weight_and_price_references():
    """The identity that makes the attribution meaningful. With b = 0 in
    price terms there is nothing to price-update, and the two indices
    coincide exactly -- so any gap between them really is the updating."""
    _pb, p0, pt, sb = _weight_reference_panel()
    report = bi.price_updating_effect(p0, pt, p0, sb)

    assert report.lowe == pytest.approx(report.young)
    assert report.difference_pp == pytest.approx(0.0)
    assert report.weight_price_change == pytest.approx(1.0)


def test_lowe_computed_from_quantities_equals_lowe_computed_from_price_updated_shares():
    """The two definitions of a Lowe index are the same index. Holding
    quantities q_b fixed and pricing them at 0 and t is arithmetically
    identical to applying b's shares, price-updated to 0, to the
    relatives -- which is why `price_updating_effect` can compute both
    legs through one function."""
    pb, p0, pt, sb = _weight_reference_panel()
    qb = sb / pb                                     # shares back to quantities

    from_quantities = bi.lowe(p0, pt, qb).value
    from_shares = bi.price_updating_effect(p0, pt, pb, sb).lowe
    assert from_quantities == pytest.approx(from_shares)


def test_price_update_shares_renormalises_and_moves_weight_to_faster_risers():
    """An item whose price rose faster than average takes a larger share
    of the same fixed basket. That is the entire mechanism."""
    pb, p0, _pt, sb = _weight_reference_panel()
    updated = bi.price_update_shares(sb, pb, p0)

    assert updated.sum() == pytest.approx(1.0)
    relatives = p0 / pb
    fastest = relatives.idxmax()
    slowest = relatives.idxmin()
    assert updated[fastest] > sb[fastest] / sb.sum()
    assert updated[slowest] < sb[slowest] / sb.sum()


def test_price_update_shares_refuses_rather_than_degrading_on_unusable_prices():
    """Zero prices in the weight reference period make every update
    ratio undefined. The old behaviour handed back the un-updated shares,
    which made `price_updating_effect` report a Lowe equal to Young and a
    price-updating effect of exactly zero: a definite answer to a question
    the data could not answer."""
    sb = pd.Series([0.5, 0.5], index=["a", "b"])
    pb = pd.Series([0.0, 0.0], index=["a", "b"])
    p0 = pd.Series([1.0, 1.0], index=["a", "b"])
    with pytest.raises(ValueError, match="cannot price-update"):
        bi.price_update_shares(sb, pb, p0)
    with pytest.raises(ValueError, match="cannot price-update"):
        bi.price_updating_effect(p0, p0 * 1.1, pb, sb)


def test_young_accepts_unnormalised_expenditure_weights():
    """Raw expenditure values may be passed directly; the formula
    normalises. A caller should not have to pre-divide by a total to get
    the right answer, because that is exactly the step people forget."""
    _pb, p0, pt, sb = _weight_reference_panel()
    raw = sb * 1000.0
    assert bi.young(p0, pt, raw).value == pytest.approx(bi.young(p0, pt, sb).value)


def test_bilateral_requirements_lists_the_inputs_each_formula_needs():
    """The interface uses this to offer only the formulae the loaded data
    can support, so a collection with no quantities is not offered a
    Fisher that would silently come back NaN."""
    assert bi.BILATERAL_REQUIREMENTS["fisher"] == ("q0", "qt")
    assert bi.BILATERAL_REQUIREMENTS["lowe"] == ("qb",)
    assert bi.BILATERAL_REQUIREMENTS["young"] == ("sb",)
    assert set(bi.BILATERAL_REQUIREMENTS) == {
        "laspeyres", "paasche", "fisher", "tornqvist", "walsh", "marshall_edgeworth",
        "geometric_laspeyres", "geometric_paasche", "lowe", "young"}
