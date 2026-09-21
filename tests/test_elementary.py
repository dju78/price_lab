"""Elementary aggregates: the result object, the three formulae added in
this phase, and the homogeneity assertion that guards the unit value
index.

The axioms (ordering, time reversal, bias) are proved on generated data
in tests/test_axioms.py, and the published values in
tests/test_golden_values.py. What is left for here is the reporting
contract -- sample size and imputation count travelling with the value --
and the one formula that can silently answer the wrong question.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pricelab.engine import elementary as el

ITEMS = ["a", "b", "c"]


def _pair():
    return (pd.Series([1.0, 2.0, 4.0], index=ITEMS),
            pd.Series([1.1, 2.4, 3.6], index=ITEMS))


# ---------------------------------------------------------------------
# The result object
# ---------------------------------------------------------------------
def test_every_formula_reports_its_own_name_and_matched_sample_size():
    a, b = _pair()
    for fn, name in [(el.jevons, "jevons"), (el.dutot, "dutot"), (el.carli, "carli"),
                     (el.harmonic_mean, "harmonic_mean"), (el.cswd, "cswd")]:
        result = fn(a, b)
        assert result.formula == name
        assert result.n_items == 3
        assert result.n_imputed == 0
        assert result.parameters == {}


def test_an_item_priced_in_only_one_period_is_not_counted_as_contributing():
    a, b = _pair()
    b = b.copy()
    b.loc["c"] = np.nan
    assert el.jevons(a, b).n_items == 2


def test_the_imputation_count_travels_with_the_value():
    """An index resting mostly on imputation is a weaker claim than the
    same index resting on collection, and the value cannot show that."""
    a, b = _pair()
    imputed = pd.Series([False, True, True], index=ITEMS)
    result = el.jevons(a, b, imputed)

    assert result.n_items == 3
    assert result.n_imputed == 2
    assert result.imputed_share == pytest.approx(2 / 3)


def test_imputed_share_is_nan_rather_than_zero_when_nothing_contributed():
    empty = pd.Series(dtype=float)
    assert np.isnan(el.jevons(empty, empty).imputed_share)


def test_no_matched_items_gives_nan_for_every_formula():
    empty = pd.Series(dtype=float)
    for fn in (el.jevons, el.dutot, el.carli, el.harmonic_mean, el.cswd):
        result = fn(empty, empty)
        assert np.isnan(result.value)
        assert result.n_items == 0


# ---------------------------------------------------------------------
# The formulae added in this phase
# ---------------------------------------------------------------------
def test_the_harmonic_mean_is_the_reciprocal_of_the_mean_reciprocal_relative():
    a, b = _pair()
    relatives = (b / a).to_numpy()
    assert el.harmonic_mean(a, b).value == pytest.approx(
        len(relatives) / (1.0 / relatives).sum())


def test_cswd_is_the_geometric_mean_of_carli_and_the_harmonic_mean():
    a, b = _pair()
    assert el.cswd(a, b).value == pytest.approx(
        np.sqrt(el.carli(a, b).value * el.harmonic_mean(a, b).value))


def test_cswd_tracks_jevons_closely():
    """Its reason for existing: an office already computing Carli can
    move to CSWD and land very close to Jevons without having to explain
    geometric means."""
    a, b = _pair()
    assert el.cswd(a, b).value == pytest.approx(el.jevons(a, b).value, rel=1e-3)


# ---------------------------------------------------------------------
# Unit value, and the assertion that guards it
# ---------------------------------------------------------------------
def test_unit_value_refuses_to_run_without_an_explicit_homogeneity_assertion():
    """The failure mode it guards against is invisible in the output, so
    it cannot be left to a default."""
    a, b = _pair()
    q = pd.Series([1.0, 1.0, 1.0], index=ITEMS)
    with pytest.raises(el.HomogeneityNotAssertedError, match="strictly homogeneous"):
        el.unit_value(a, b, q, q)


def test_unit_value_refuses_an_unexplained_assertion():
    """An assertion nobody has to justify is not reviewable, and the
    whole point of recording it is that a reviewer can judge it."""
    a, b = _pair()
    q = pd.Series([1.0, 1.0, 1.0], index=ITEMS)
    with pytest.raises(el.HomogeneityNotAssertedError, match="justification"):
        el.unit_value(a, b, q, q, homogeneous=True)
    with pytest.raises(el.HomogeneityNotAssertedError, match="justification"):
        el.unit_value(a, b, q, q, homogeneous=True, homogeneity_justification="   ")


def test_unit_value_records_the_assertion_that_licensed_it():
    a, b = _pair()
    q = pd.Series([1.0, 1.0, 1.0], index=ITEMS)
    result = el.unit_value(a, b, q, q, homogeneous=True,
                           homogeneity_justification="single SKU, one pack size")

    assert result.formula == "unit_value"
    assert result.parameters["homogeneous"] is True
    assert result.parameters["homogeneity_justification"] == "single SKU, one pack size"


def test_unit_value_is_the_ratio_of_expenditure_per_unit():
    items = ["x", "y"]
    p0 = pd.Series([2.0, 4.0], index=items)
    pt = pd.Series([3.0, 5.0], index=items)
    q0 = pd.Series([10.0, 10.0], index=items)
    qt = pd.Series([10.0, 10.0], index=items)

    result = el.unit_value(p0, pt, q0, qt, homogeneous=True,
                           homogeneity_justification="test")
    assert result.value == pytest.approx((3.0 + 5.0) / (2.0 + 4.0))


def test_unit_value_moves_on_a_pure_mix_shift_which_is_why_homogeneity_matters():
    """Applied to a set that is not homogeneous it is not biased so much
    as meaningless: with both prices unchanged, shifting purchases from
    the cheap item to the dear one makes it rise, having measured nothing
    about prices at all. This is the failure the assertion guards, shown
    rather than described."""
    items = ["cheap", "dear"]
    prices = pd.Series([1.0, 10.0], index=items)
    q0 = pd.Series([90.0, 10.0], index=items)
    qt = pd.Series([10.0, 90.0], index=items)

    result = el.unit_value(prices, prices, q0, qt, homogeneous=True,
                           homogeneity_justification="deliberately false, for the test")
    assert result.value > 4.0


def test_unit_value_is_nan_without_usable_quantities():
    a, b = _pair()
    zero = pd.Series([0.0, 0.0, 0.0], index=ITEMS)
    result = el.unit_value(a, b, zero, zero, homogeneous=True,
                           homogeneity_justification="test")
    assert np.isnan(result.value)


def test_unit_value_is_nan_with_nothing_matched_but_still_records_the_assertion():
    empty = pd.Series(dtype=float)
    result = el.unit_value(empty, empty, empty, empty, homogeneous=True,
                           homogeneity_justification="test")
    assert np.isnan(result.value)
    assert result.parameters["homogeneous"] is True


# ---------------------------------------------------------------------
# The registry of price-only formulae
# ---------------------------------------------------------------------
def test_unit_value_is_not_selectable_by_name():
    """It needs quantities and an explicit assertion, so it cannot be
    picked from a dropdown the way the others can. That is the point."""
    assert set(el.ELEMENTARY_FORMULAE) == {
        "jevons", "dutot", "carli", "harmonic_mean", "cswd"}
    assert "unit_value" not in el.ELEMENTARY_FORMULAE


def test_the_shipped_formulae_agree_with_the_engines_own_implementations():
    """jevons, dutot and carli are not reimplemented here -- they call
    engine.index, which is what the rest of the engine and the existing
    test suite use."""
    from pricelab.engine.index import carli, dutot, jevons

    a, b = _pair()
    assert el.jevons(a, b).value == pytest.approx(jevons(a, b))
    assert el.dutot(a, b).value == pytest.approx(dutot(a, b))
    assert el.carli(a, b).value == pytest.approx(carli(a, b))
