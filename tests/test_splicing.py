"""Chain linking, rebasing, link factors and the chain drift diagnostic.

The property under test throughout is which operations may change a
movement and which may not. Rebasing and linking are rescalings and must
leave every period-on-period ratio untouched; chaining is a different
computation and the gap it opens against the direct comparison is the
thing the drift diagnostic exists to surface.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pricelab.core.config import IndexConfig
from pricelab.engine import splicing as sp


def _series(values: list[float], start: str = "2020-01-01") -> pd.Series:
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="MS"))


# ---------------------------------------------------------------------
# Rebasing
# ---------------------------------------------------------------------
def test_rebasing_sets_the_reference_period_to_base_value():
    s = _series([100.0, 105.0, 110.0, 99.0])
    out = sp.rebase(s, s.index[2])
    assert out.loc[s.index[2]] == pytest.approx(100.0)


def test_rebasing_changes_every_level_but_no_movement():
    """The defining property: multiplying by a constant cancels out of
    every ratio, so growth rates are identical before and after."""
    s = _series([100.0, 105.0, 110.0, 99.0])
    out = sp.rebase(s, s.index[2])

    assert not np.allclose(out.to_numpy(), s.to_numpy())
    pd.testing.assert_series_equal(out.pct_change(), s.pct_change())


def test_rebasing_to_an_absent_period_is_refused():
    s = _series([100.0, 105.0])
    with pytest.raises(ValueError, match="not in the series"):
        sp.rebase(s, pd.Timestamp("2021-07-01"))


def test_rebasing_on_a_non_finite_divisor_is_refused():
    s = _series([np.nan, 105.0])
    with pytest.raises(ValueError, match="cannot"):
        sp.rebase(s, s.index[0])


def test_rebase_to_config_uses_the_engines_own_resolution_rule():
    """A series rebased here and one rebased by build_index must agree
    about which period "= 100" refers to, including the price-reference
    fallback added in this phase."""
    s = _series([100.0, 105.0, 110.0, 99.0])
    cfg = IndexConfig(price_reference_period=str(s.index[2].date()))
    out = sp.rebase_to_config(s, cfg)
    assert out.loc[s.index[2]] == pytest.approx(100.0)


# ---------------------------------------------------------------------
# Link factors and splicing
# ---------------------------------------------------------------------
def test_link_factor_is_the_ratio_at_the_overlap():
    old = _series([100.0, 110.0, 120.0])
    new = _series([100.0, 105.0, 115.0], start="2020-03-01")
    factor = sp.link_factor(old, new, pd.Timestamp("2020-03-01"))
    assert factor == pytest.approx(120.0 / 100.0)


def test_link_factor_refuses_a_period_absent_from_either_series():
    old = _series([100.0, 110.0])
    new = _series([100.0, 105.0], start="2020-05-01")
    with pytest.raises(ValueError, match="not present in the old series"):
        sp.link_factor(old, new, pd.Timestamp("2020-05-01"))


def test_splicing_joins_without_a_jump_and_preserves_the_new_series_movements():
    old = _series([100.0, 110.0, 120.0])
    new = _series([100.0, 105.0, 115.0], start="2020-03-01")
    joined = sp.splice(old, new, pd.Timestamp("2020-03-01"))

    # The join reads the old series' level, not the new one's.
    assert joined.loc[pd.Timestamp("2020-03-01")] == pytest.approx(120.0)
    # The new series' own movements survive the rescaling exactly.
    after = joined.loc[joined.index >= pd.Timestamp("2020-03-01")]
    pd.testing.assert_series_equal(
        after.pct_change().reset_index(drop=True),
        new.pct_change().reset_index(drop=True))
    # No period is duplicated at the join.
    assert joined.index.is_unique


# ---------------------------------------------------------------------
# Chaining
# ---------------------------------------------------------------------
def test_chaining_compounds_links_into_a_level():
    links = _series([np.nan, 1.10, 1.05, 0.90])
    out = sp.chain(links)
    assert out.tolist() == pytest.approx([100.0, 110.0, 115.5, 103.95])


def test_chaining_holds_the_level_across_a_non_finite_link():
    """Matches build_index: a period with too few matched items to
    compare holds the level rather than destroying the rest of the
    series."""
    links = _series([np.nan, 1.10, np.nan, 1.05])
    out = sp.chain(links)
    assert out.tolist() == pytest.approx([100.0, 110.0, 110.0, 115.5])


def test_price_update_reports_the_ratio_between_two_periods():
    s = _series([100.0, 110.0, 121.0])
    assert sp.price_update(s, s.index[0], s.index[2]) == pytest.approx(1.21)


def test_price_update_refuses_an_absent_period():
    s = _series([100.0, 110.0])
    with pytest.raises(ValueError, match="not present"):
        sp.price_update(s, s.index[0], pd.Timestamp("2030-01-01"))


# ---------------------------------------------------------------------
# Chain drift
# ---------------------------------------------------------------------
def test_chain_drift_is_zero_when_the_two_series_agree():
    s = _series([100.0, 105.0, 110.0])
    report = sp.chain_drift(s, s)
    assert report.drift_pp == pytest.approx(0.0)
    assert not report.exceeds_threshold


def test_chain_drift_measures_the_gap_and_flags_it_against_the_threshold():
    chained = _series([100.0, 105.0, 112.0])
    direct = _series([100.0, 105.0, 110.0])

    report = sp.chain_drift(chained, direct, threshold_pp=1.0)
    assert report.drift_pp == pytest.approx(2.0)
    assert report.drift_pct == pytest.approx(2.0 / 110.0 * 100.0)
    assert report.exceeds_threshold
    assert "above the direct index" in report.message


def test_chain_drift_threshold_is_configurable():
    chained = _series([100.0, 105.0, 112.0])
    direct = _series([100.0, 105.0, 110.0])
    assert not sp.chain_drift(chained, direct, threshold_pp=5.0).exceeds_threshold
    assert sp.chain_drift(chained, direct, threshold_pp=0.5).exceeds_threshold


def test_chain_drift_rebases_both_series_before_comparing():
    """Two series differing only in what they were rebased to have no
    drift between them: otherwise this diagnostic would report the
    reference-period choice as if it were an accumulation error."""
    direct = _series([100.0, 105.0, 110.0])
    chained_rebased_elsewhere = direct / 105.0 * 100.0
    assert sp.chain_drift(chained_rebased_elsewhere, direct).drift_pp == pytest.approx(0.0)


def test_chain_drift_needs_a_span_to_measure_over():
    one = _series([100.0])
    with pytest.raises(ValueError, match="at least two periods"):
        sp.chain_drift(one, one)


def test_chain_drift_table_ranks_by_absolute_drift_and_flags_each_row():
    periods = pd.date_range("2020-01-01", periods=3, freq="MS")
    chained = pd.DataFrame({"Bread": [100.0, 105.0, 112.0],
                            "Milk": [100.0, 99.0, 98.2]}, index=periods)
    direct = pd.DataFrame({"Bread": [100.0, 105.0, 110.0],
                           "Milk": [100.0, 99.0, 98.0]}, index=periods)

    table = sp.chain_drift_table(chained, direct, threshold_pp=1.0)
    assert list(table.index) == ["Bread", "Milk"]          # ranked by |drift|
    assert bool(table.loc["Bread", "exceeds_threshold"])
    assert not bool(table.loc["Milk", "exceeds_threshold"])


def test_chain_drift_table_is_empty_rather_than_raising_when_nothing_is_comparable():
    periods = pd.date_range("2020-01-01", periods=3, freq="MS")
    chained = pd.DataFrame({"Bread": [100.0, 105.0, 112.0]}, index=periods)
    direct = pd.DataFrame({"Milk": [100.0, 99.0, 98.0]}, index=periods)
    assert sp.chain_drift_table(chained, direct).empty


# ---------------------------------------------------------------------
# The property the whole module is about, end to end
# ---------------------------------------------------------------------
def test_a_transitive_formula_has_exactly_zero_chain_drift_through_the_real_engine():
    """Jevons chains to its own direct counterpart, so the drift
    diagnostic run over a real build_index result reports zero -- the
    engine-level counterpart of the CPI Manual's Table 8.3 identity."""
    from pricelab.engine.index import build_index

    periods = pd.date_range("2020-01-01", periods=6, freq="MS")
    df = pd.DataFrame({
        "period": list(periods) * 2,
        "item_id": ["1"] * 6 + ["2"] * 6,
        "price_imputed": [10.0, 11.0, 9.0, 12.0, 11.5, 10.0,
                          20.0, 19.0, 22.0, 26.0, 24.0, 20.0],
    })
    chained = build_index(df, IndexConfig(formula="jevons", chained=True))["index"]
    direct = build_index(df, IndexConfig(formula="jevons", chained=False))["index"]

    report = sp.chain_drift(chained, direct)
    assert report.drift_pp == pytest.approx(0.0, abs=1e-9)
    assert not report.exceeds_threshold


def test_an_intransitive_formula_shows_real_chain_drift_through_the_real_engine():
    """Carli does not chain to its direct counterpart, and on prices that
    return exactly to their starting point the chained series ends above
    100 while the direct series ends at exactly 100."""
    from pricelab.engine.index import build_index

    periods = pd.date_range("2020-01-01", periods=4, freq="MS")
    df = pd.DataFrame({
        "period": list(periods) * 2,
        "item_id": ["1"] * 4 + ["2"] * 4,
        "price_imputed": [10.0, 20.0, 5.0, 10.0,
                          20.0, 10.0, 40.0, 20.0],
    })
    chained = build_index(df, IndexConfig(formula="carli", chained=True))["index"]
    direct = build_index(df, IndexConfig(formula="carli", chained=False))["index"]

    assert direct.iloc[-1] == pytest.approx(100.0)
    assert chained.iloc[-1] > 100.0
    report = sp.chain_drift(chained, direct, threshold_pp=1.0)
    assert report.exceeds_threshold
