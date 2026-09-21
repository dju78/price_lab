"""The axiomatic tests: properties an index formula either has or does
not, checked on generated data rather than on a convenient example.

A worked example proves a formula produces the right number on one data
set. An axiom proves it produces the right *kind* of number on every data
set, which is the claim actually being made when a formula is chosen.
Hypothesis generates the data, so the tests are looking for the
counterexample rather than confirming the case the author happened to
think of.

Two kinds of assertion appear here and they are not interchangeable:

  - exact, to machine precision: identity, proportionality,
    commensurability, time reversal for the formulae that satisfy it, and
    factor reversal for Fisher. These are algebraic identities; a failure
    is a bug, not a tolerance question.
  - directional: the ordering and bias tests. These hold by theorem given
    a stated condition on the data, and the test constructs data meeting
    that condition rather than hoping random data happens to.

Failures are also asserted, deliberately: Carli must fail time reversal
and Dutot must fail commensurability. A version of this suite that only
tested the passing cases would go green if every formula were silently
replaced by Jevons.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from pricelab.engine import bilateral as bi
from pricelab.engine import elementary as el

SLOW = settings(max_examples=60, deadline=None,
                suppress_health_check=[HealthCheck.too_slow])

# Bounded well away from zero and from each other's extremes: the axioms
# below are exact in real arithmetic, and a price of 1e-300 next to one of
# 1e300 tests floating-point representation rather than the index formula.
prices = st.floats(min_value=0.05, max_value=5_000, allow_nan=False, allow_infinity=False)
quantities = st.floats(min_value=0.5, max_value=50_000, allow_nan=False, allow_infinity=False)


@st.composite
def price_quantity_panels(draw: st.DrawFn, min_items: int = 2, max_items: int = 8):
    """Two periods of prices and quantities over the same items."""
    n = draw(st.integers(min_value=min_items, max_value=max_items))
    items = [f"i{k}" for k in range(n)]
    p0 = draw(st.lists(prices, min_size=n, max_size=n))
    pt = draw(st.lists(prices, min_size=n, max_size=n))
    q0 = draw(st.lists(quantities, min_size=n, max_size=n))
    qt = draw(st.lists(quantities, min_size=n, max_size=n))
    return (pd.Series(p0, index=items), pd.Series(pt, index=items),
            pd.Series(q0, index=items), pd.Series(qt, index=items))


@st.composite
def price_panels(draw: st.DrawFn, min_items: int = 2, max_items: int = 8):
    n = draw(st.integers(min_value=min_items, max_value=max_items))
    items = [f"i{k}" for k in range(n)]
    p0 = draw(st.lists(prices, min_size=n, max_size=n))
    pt = draw(st.lists(prices, min_size=n, max_size=n))
    return pd.Series(p0, index=items), pd.Series(pt, index=items)


# ---------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------
@given(price_quantity_panels())
@SLOW
def test_identity_every_formula_returns_one_when_no_price_changed(panel):
    """Unchanged prices give an index of exactly 1 (100), whatever the
    quantities did. A formula that moves here is reporting quantity
    change as price change."""
    p0, _pt, q0, qt = panel
    pt = p0.copy()

    for result in (
        el.jevons(p0, pt), el.dutot(p0, pt), el.carli(p0, pt),
        el.harmonic_mean(p0, pt), el.cswd(p0, pt),
        bi.laspeyres(p0, pt, q0), bi.paasche(p0, pt, qt),
        bi.fisher(p0, pt, q0, qt), bi.tornqvist(p0, pt, q0, qt),
        bi.walsh(p0, pt, q0, qt), bi.marshall_edgeworth(p0, pt, q0, qt),
        bi.geometric_laspeyres(p0, pt, q0), bi.geometric_paasche(p0, pt, qt),
        bi.lowe(p0, pt, q0), bi.young(p0, pt, q0),
    ):
        assert result.value == pytest.approx(1.0, rel=1e-12), result.formula


# ---------------------------------------------------------------------
# Proportionality
# ---------------------------------------------------------------------
@given(price_quantity_panels(), st.floats(min_value=0.05, max_value=20, allow_nan=False))
@SLOW
def test_proportionality_scaling_every_price_by_k_scales_the_index_by_k(panel, k):
    """All prices multiplied by k gives an index of exactly k (100k)."""
    p0, _pt, q0, qt = panel
    pt = p0 * k

    for result in (
        el.jevons(p0, pt), el.dutot(p0, pt), el.carli(p0, pt),
        el.harmonic_mean(p0, pt), el.cswd(p0, pt),
        bi.laspeyres(p0, pt, q0), bi.paasche(p0, pt, qt),
        bi.fisher(p0, pt, q0, qt), bi.tornqvist(p0, pt, q0, qt),
        bi.walsh(p0, pt, q0, qt), bi.marshall_edgeworth(p0, pt, q0, qt),
        bi.geometric_laspeyres(p0, pt, q0), bi.geometric_paasche(p0, pt, qt),
        bi.lowe(p0, pt, q0), bi.young(p0, pt, q0),
    ):
        assert result.value == pytest.approx(k, rel=1e-9), result.formula


# ---------------------------------------------------------------------
# Commensurability
# ---------------------------------------------------------------------
@given(price_quantity_panels(min_items=2, max_items=6),
       st.floats(min_value=0.02, max_value=50, allow_nan=False))
@SLOW
def test_commensurability_changing_one_items_unit_leaves_jevons_fisher_tornqvist_alone(
        panel, unit_change):
    """Re-express one item in different units -- price per gram instead of
    per kilo -- and the index must not move.

    Changing the unit means dividing the price by `unit_change` and
    multiplying the quantity by it, so the item's expenditure is
    untouched: nothing about the world changed, only the notation. Jevons
    works on relatives, and Fisher and Tornqvist work on price-quantity
    products, so all three are blind to it.
    """
    p0, pt, q0, qt = panel
    first = p0.index[0]

    def rescale(series: pd.Series, factor: float) -> pd.Series:
        out = series.copy()
        out.loc[first] = out.loc[first] * factor
        return out

    p0u, ptu = rescale(p0, 1 / unit_change), rescale(pt, 1 / unit_change)
    q0u, qtu = rescale(q0, unit_change), rescale(qt, unit_change)

    assert el.jevons(p0u, ptu).value == pytest.approx(el.jevons(p0, pt).value, rel=1e-9)
    assert bi.fisher(p0u, ptu, q0u, qtu).value == pytest.approx(
        bi.fisher(p0, pt, q0, qt).value, rel=1e-9)
    assert bi.tornqvist(p0u, ptu, q0u, qtu).value == pytest.approx(
        bi.tornqvist(p0, pt, q0, qt).value, rel=1e-9)


def test_commensurability_dutot_is_shown_to_fail_it():
    """Dutot does move, and the test says so rather than omitting it.

    Two items, one price doubling and one halving, so every relative-based
    index is unmoved by construction. Re-expressing item B in units a
    hundred times smaller makes its price dominate the arithmetic mean,
    and Dutot swings by more than 60 points on data where no price
    changed at all. This is the concrete reason the CPI Manual restricts
    Dutot to homogeneous aggregates.
    """
    items = ["A", "B"]
    p0 = pd.Series([1.0, 10.0], index=items)
    pt = pd.Series([2.0, 5.0], index=items)

    before = el.dutot(p0, pt).value
    p0u = pd.Series([1.0, 10.0 / 100], index=items)
    ptu = pd.Series([2.0, 5.0 / 100], index=items)
    after = el.dutot(p0u, ptu).value

    assert before == pytest.approx(7.0 / 11.0)
    assert after == pytest.approx(2.05 / 1.10)
    assert abs(after - before) > 0.6

    # ...while Jevons, on the same rescaling, does not move at all.
    assert el.jevons(p0u, ptu).value == pytest.approx(el.jevons(p0, pt).value, rel=1e-12)


# ---------------------------------------------------------------------
# Time reversal
# ---------------------------------------------------------------------
@given(price_quantity_panels())
@SLOW
def test_time_reversal_holds_to_machine_precision_for_the_formulae_that_satisfy_it(panel):
    """P(0->t) * P(t->0) == 1 exactly, for Fisher, Tornqvist, Jevons,
    Dutot and CSWD."""
    p0, pt, q0, qt = panel

    pairs = [
        ("jevons", el.jevons(p0, pt).value, el.jevons(pt, p0).value),
        ("dutot", el.dutot(p0, pt).value, el.dutot(pt, p0).value),
        ("cswd", el.cswd(p0, pt).value, el.cswd(pt, p0).value),
        ("fisher", bi.fisher(p0, pt, q0, qt).value, bi.fisher(pt, p0, qt, q0).value),
        ("tornqvist", bi.tornqvist(p0, pt, q0, qt).value, bi.tornqvist(pt, p0, qt, q0).value),
    ]
    for name, forward, backward in pairs:
        assert forward * backward == pytest.approx(1.0, rel=1e-9), name


def test_time_reversal_carli_and_the_harmonic_mean_are_shown_to_fail_it():
    """Carli fails upward and the harmonic mean fails downward, by the
    same factor -- they are time antitheses of each other.

    That symmetry is not decoration: it is why CSWD, their geometric mean,
    passes. The test asserts all three facts together, because the first
    two on their own would be satisfied by any pair of broken formulae.
    """
    items = ["A", "B"]
    p0 = pd.Series([1.0, 1.0], index=items)
    pt = pd.Series([0.5, 2.0], index=items)

    carli_product = el.carli(p0, pt).value * el.carli(pt, p0).value
    harmonic_product = el.harmonic_mean(p0, pt).value * el.harmonic_mean(pt, p0).value

    assert carli_product > 1.0            # upward bias, compounding both ways
    assert harmonic_product < 1.0         # downward bias
    assert carli_product == pytest.approx(1.5625)
    assert harmonic_product == pytest.approx(1 / 1.5625)
    # Antitheses: the two failures are exact reciprocals...
    assert carli_product * harmonic_product == pytest.approx(1.0, rel=1e-12)
    # ...so their geometric mean, CSWD, passes.
    assert el.cswd(p0, pt).value * el.cswd(pt, p0).value == pytest.approx(1.0, rel=1e-12)


# ---------------------------------------------------------------------
# Factor reversal
# ---------------------------------------------------------------------
@given(price_quantity_panels())
@SLOW
def test_factor_reversal_fisher_price_times_fisher_quantity_is_the_value_ratio(panel):
    """The property that earned Fisher the name "ideal": the price index
    and the quantity index built the same way multiply to the change in
    total expenditure, leaving no residual to explain.

    No other index here satisfies it -- Tornqvist is checked below as a
    contrast so the test cannot pass by the assertion being vacuous.
    """
    p0, pt, q0, qt = panel

    price = bi.fisher(p0, pt, q0, qt).value
    quantity = bi.fisher_quantity(p0, pt, q0, qt).value
    assert price * quantity == pytest.approx(bi.value_ratio(p0, pt, q0, qt), rel=1e-9)


def test_factor_reversal_is_not_satisfied_by_tornqvist():
    """A contrast case: Tornqvist satisfies time reversal but not factor
    reversal, so the factor reversal test above is testing something
    Fisher specifically has."""
    items = ["A", "B"]
    p0 = pd.Series([1.0, 2.0], index=items)
    pt = pd.Series([3.0, 2.0], index=items)
    q0 = pd.Series([10.0, 1.0], index=items)
    qt = pd.Series([1.0, 10.0], index=items)

    tornqvist_price = bi.tornqvist(p0, pt, q0, qt).value
    implied_quantity = bi.value_ratio(p0, pt, q0, qt) / tornqvist_price
    fisher_quantity = bi.fisher_quantity(p0, pt, q0, qt).value
    assert implied_quantity != pytest.approx(fisher_quantity, rel=1e-6)


# ---------------------------------------------------------------------
# Ordering and substitution bias
# ---------------------------------------------------------------------
@given(price_quantity_panels(min_items=3, max_items=8))
@SLOW
def test_ordering_laspeyres_exceeds_fisher_exceeds_paasche_under_substitution(panel):
    """With consumers substituting away from what got relatively dearer,
    Laspeyres >= Fisher >= Paasche.

    The condition is a negative correlation between price change and
    quantity change, which is what "substitution" means; the test
    constructs it rather than hoping for it, by setting each item's
    quantity change to the inverse of its price relative. Fisher lies
    between the two by construction (it is their geometric mean), so the
    content of the test is the outer inequality: that Laspeyres, holding
    the old basket, overstates against Paasche, holding the new one.
    """
    p0, pt, q0, _qt = panel
    relatives = pt / p0
    assume(relatives.std() > 1e-6)     # nothing to substitute away from otherwise
    qt = q0 / relatives                # exact unit-elasticity substitution

    laspeyres = bi.laspeyres(p0, pt, q0).value
    paasche = bi.paasche(p0, pt, qt).value
    fisher = bi.fisher(p0, pt, q0, qt).value

    assert laspeyres >= fisher - 1e-12
    assert fisher >= paasche - 1e-12
    assert bi.substitution_bias_pp(laspeyres, paasche) >= -1e-9


def test_substitution_bias_is_quantified_not_merely_signed():
    """The gap between Laspeyres and Paasche is reported as a number, in
    percentage points, and Fisher sits at their geometric mean."""
    items = ["A", "B"]
    p0 = pd.Series([1.0, 1.0], index=items)
    pt = pd.Series([2.0, 1.0], index=items)
    q0 = pd.Series([10.0, 10.0], index=items)
    qt = pd.Series([5.0, 15.0], index=items)      # substituted away from A

    result = bi.fisher(p0, pt, q0, qt)
    laspeyres = result.components["laspeyres"]
    paasche = result.components["paasche"]

    assert laspeyres == pytest.approx(1.5)        # (2*10 + 1*10) / (1*10 + 1*10)
    assert paasche == pytest.approx(1.25)         # (2*5 + 1*15) / (1*5 + 1*15)
    assert result.components["substitution_bias_pp"] == pytest.approx(25.0)
    assert result.value == pytest.approx(np.sqrt(1.5 * 1.25))


# ---------------------------------------------------------------------
# Elementary bias
# ---------------------------------------------------------------------
@given(price_panels(min_items=3, max_items=10))
@SLOW
def test_elementary_bias_carli_exceeds_jevons_exceeds_the_harmonic_mean(panel):
    """arithmetic >= geometric >= harmonic, always (CPI Manual 2020,
    paragraph 8.85), with equality only when every relative is identical."""
    p0, pt = panel
    carli = el.carli(p0, pt).value
    jevons = el.jevons(p0, pt).value
    harmonic = el.harmonic_mean(p0, pt).value

    assert carli >= jevons - 1e-12
    assert jevons >= harmonic - 1e-12


@pytest.mark.parametrize("sigma", [0.02, 0.05, 0.10])
def test_elementary_bias_gap_matches_the_theoretical_relationship(sigma):
    """The gaps are not merely ordered, they are the size theory says.

    For log price relatives with variance s^2, a second-order expansion
    gives ln(Carli) - ln(Jevons) ~= s^2 / 2 and ln(Jevons) - ln(harmonic)
    ~= s^2 / 2. Two consequences are checked, both of which would fail if
    a formula were subtly wrong rather than merely mis-ordered:

      1. each gap matches s^2 / 2, to a tolerance that tightens with s;
      2. the two gaps are near-equal, so Jevons sits close to the midpoint
         -- which is the same statement as CSWD approximating Jevons, and
         is checked directly as well.

    A log-normal spread of relatives with known variance is constructed
    for each s, so "theoretical relationship" means a number computed from
    the distribution rather than a constant taken on faith.
    """
    rng = np.random.default_rng(20260920)
    n = 20_000
    log_relatives = rng.normal(0.0, sigma, n)
    log_relatives -= log_relatives.mean()          # centre, so Jevons == 1 exactly
    sample_variance = float(log_relatives.var())

    items = [f"i{k}" for k in range(n)]
    p0 = pd.Series(np.ones(n), index=items)
    pt = pd.Series(np.exp(log_relatives), index=items)

    carli = el.carli(p0, pt).value
    jevons = el.jevons(p0, pt).value
    harmonic = el.harmonic_mean(p0, pt).value
    cswd = el.cswd(p0, pt).value

    assert jevons == pytest.approx(1.0, abs=1e-12)

    predicted_gap = sample_variance / 2.0
    upper_gap = np.log(carli) - np.log(jevons)
    lower_gap = np.log(jevons) - np.log(harmonic)

    # Second-order prediction: error is O(s^4), so scale the tolerance
    # with s^2 rather than using one absolute number across all three.
    tolerance = 2.0 * sigma**2
    assert upper_gap == pytest.approx(predicted_gap, abs=tolerance * predicted_gap + 1e-12)
    assert lower_gap == pytest.approx(predicted_gap, abs=tolerance * predicted_gap + 1e-12)

    # The two gaps are symmetric about Jevons, which is why CSWD -- the
    # geometric mean of the two biased formulae -- tracks Jevons closely.
    assert cswd == pytest.approx(jevons, rel=sigma**2)


# ---------------------------------------------------------------------
# Additivity
# ---------------------------------------------------------------------
@st.composite
def four_level_trees(draw: st.DrawFn):
    """A random four-level classification tree with random leaf weights
    and random leaf index series.

    Four levels because that is where additivity stops being obvious: one
    level is a weighted mean, and the question is whether the weights
    derived by summing up three levels still make the leaves'
    contributions add to the root's change.
    """
    parent_of: dict[str, str | None] = {"root": None}
    leaves: list[str] = []

    for division in range(draw(st.integers(min_value=2, max_value=3))):
        d = f"d{division}"
        parent_of[d] = "root"
        for group in range(draw(st.integers(min_value=1, max_value=3))):
            g = f"{d}_g{group}"
            parent_of[g] = d
            for klass in range(draw(st.integers(min_value=1, max_value=3))):
                c = f"{g}_c{klass}"
                parent_of[c] = g
                leaves.append(c)

    periods = pd.date_range("2020-01-01", periods=3, freq="MS")
    levels = draw(st.lists(
        st.floats(min_value=50, max_value=250, allow_nan=False, allow_infinity=False),
        min_size=len(leaves) * len(periods), max_size=len(leaves) * len(periods)))
    leaf_indices = pd.DataFrame(
        np.array(levels).reshape(len(periods), len(leaves)), index=periods, columns=leaves)

    weights = draw(st.lists(
        st.floats(min_value=0.01, max_value=100, allow_nan=False, allow_infinity=False),
        min_size=len(leaves), max_size=len(leaves)))
    return leaf_indices, dict(zip(leaves, weights, strict=True)), parent_of, periods


@given(four_level_trees())
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_additivity_contributions_sum_to_the_headline_change(tree):
    """Every leaf's contribution sums to the root's percentage change, to
    eight decimal places.

    Checked at two levels of the tree, because a decomposition that adds
    up one level down but not three is a decomposition that works only
    where nobody looks: the leaves' contributions must reconstruct the
    headline, and so must the divisions'.
    """
    from pricelab.engine.aggregation import aggregate_tree, contributions

    leaf_indices, leaf_weights, parent_of, periods = tree
    result = aggregate_tree(leaf_indices, leaf_weights, parent_of)
    start, end = periods[0], periods[-1]

    root = result.indices["root"]
    headline_pct = (root.loc[end] / root.loc[start] - 1.0) * 100.0

    leaf_contributions = contributions(
        result.indices[list(leaf_weights)], result.weights.to_dict(), start, end)
    assert leaf_contributions.sum() == pytest.approx(headline_pct, abs=1e-8)

    divisions = [n for n, parent in parent_of.items() if parent == "root"]
    division_contributions = contributions(
        result.indices[divisions], result.weights.to_dict(), start, end)
    assert division_contributions.sum() == pytest.approx(headline_pct, abs=1e-8)


@given(four_level_trees())
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_a_parents_weight_is_the_sum_of_its_childrens(tree):
    """The invariant that makes the above work: aggregation derives each
    parent's weight by summing its children's, so no level of the tree
    can be weighted inconsistently with the level below it."""
    from pricelab.engine.aggregation import aggregate_tree

    leaf_indices, leaf_weights, parent_of, _periods = tree
    result = aggregate_tree(leaf_indices, leaf_weights, parent_of)

    assert result.problems == []
    assert result.weights["root"] == pytest.approx(sum(leaf_weights.values()))
    for node, parent in parent_of.items():
        if parent is None:
            continue
        children = [n for n, p in parent_of.items() if p == node]
        if children:
            assert result.weights[node] == pytest.approx(
                sum(result.weights[c] for c in children))
