"""Golden values: this engine reproduces the published worked example in
the CPI Manual 2020, number for number.

Source
------
Consumer Price Index Manual: Concepts and Methods (IMF/ILO/OECD/Eurostat/
UNECE/World Bank, 2020), Chapter 8 "Calculating Consumer Price Indices in
Practice", paragraphs 8.16-8.20 and Tables 8.1, 8.2 and 8.3. Retrieved
2026-09-20 from the ILO's copy of the published volume:
https://www.ilo.org/sites/default/files/wcmsp5/groups/public/@dgreports/
@stat/documents/publication/wcms_761444.pdf

The example is an elementary aggregate of seven varieties priced in a
base period and in each of seven months, with a complete set of prices
(no missing, no replacement).

Precision, and why it is not six decimal places
-----------------------------------------------
The phase asked for agreement to six decimals. The manual does not
publish six: its own footnote to paragraph 8.20 states that "the results
in the following tables are rounded to three decimals for aggregate price
relatives and one decimal for price indices", the underlying figures
having been computed in a spreadsheet that is not published. Asserting
six decimals against a source that carries one would not be a stronger
test, it would be a fabricated one -- the extra five digits would have
had to come from this implementation rather than from the manual, making
the test a check that the code agrees with itself.

So these tests assert agreement at the manual's own published precision,
and say so. Two further consequences are visible below and are recorded
rather than worked around:

  - Table 8.1's geometric mean price for March prints as 4.81 where the
    printed prices give 4.80. That is the manual's own rounding: the
    prices are published to two decimals and the means were computed from
    unrounded ones. The index values, which are what the tables exist to
    demonstrate, are unaffected and all match.
  - Table 8.2's long-term relatives for June print as 1.100 for every
    variety, because June's prices really are exactly 1.1 times the base
    period's; computed from the two-decimal printed prices, three of the
    seven come out as 1.097 or 1.101. Again the indices match.

Superlative indices are not covered here, deliberately -- see
`test_no_superlative_golden_values_are_asserted` at the end of this file.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pricelab.engine import bilateral as bi
from pricelab.engine import elementary as el

#: CPI Manual 2020, Table 8.1, "Item A Prices". Columns are the base
#: period followed by January to July.
COLUMNS = ["Base", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul"]
PRICES = pd.DataFrame(
    [
        [2.36, 2.09, 1.93, 2.59, 2.05, 2.85, 2.59, 2.36],
        [5.02, 5.38, 5.12, 5.52, 4.08, 4.08, 5.52, 5.02],
        [5.34, 5.07, 5.09, 5.88, 6.29, 5.86, 5.88, 5.34],
        [6.00, 5.73, 4.27, 6.00, 4.75, 5.27, 6.60, 6.00],
        [6.12, 6.39, 5.50, 6.12, 5.86, 6.29, 6.74, 6.12],
        [2.80, 2.72, 2.82, 3.08, 2.85, 2.05, 3.08, 2.80],
        [6.21, 5.45, 6.95, 6.21, 5.27, 4.75, 6.84, 6.21],
    ],
    index=[f"Variety {i}" for i in range(1, 8)],
    columns=COLUMNS,
)

#: Table 8.1: Jevons and Dutot as long-term indices on the base period.
JEVONS_LONG_TERM = [100.0, 96.3, 92.4, 105.6, 91.7, 91.7, 110.0, 100.0]
DUTOT_LONG_TERM = [100.0, 97.0, 93.6, 104.6, 92.0, 92.0, 110.0, 100.0]

#: Table 8.2: Carli as a long-term index on the base period.
CARLI_LONG_TERM = [100.0, 96.5, 93.3, 105.7, 92.5, 93.2, 110.0, 100.0]

#: Table 8.3: chained short-term indices, January to July. The Carli row
#: is the manual's demonstration of intransitivity.
JEVONS_CHAINED = [96.3, 92.4, 105.6, 91.7, 91.7, 110.0, 100.0]
CARLI_CHAINED = [96.5, 93.7, 108.1, 94.6, 96.3, 117.4, 106.7]

#: Table 8.1's published mean price rows.
GEOMETRIC_MEAN_PRICES = [4.55, 4.38, 4.20, 4.81, 4.17, 4.17, 5.01, 4.55]
ARITHMETIC_MEAN_PRICES = [4.84, 4.69, 4.53, 5.06, 4.45, 4.45, 5.32, 4.84]


def _column(name: str) -> pd.Series:
    return PRICES[name]


# ---------------------------------------------------------------------
# Table 8.1: Jevons and Dutot on averages of prices
# ---------------------------------------------------------------------
def test_table_8_1_jevons_long_term_index():
    """CPI Manual 2020, Table 8.1, "Jevons Index (L-T ratio of geometric
    mean prices)"."""
    base = _column("Base")
    computed = [round(el.jevons(base, _column(c)).value * 100, 1) for c in COLUMNS]
    assert computed == JEVONS_LONG_TERM


def test_table_8_1_dutot_long_term_index():
    """CPI Manual 2020, Table 8.1, "Dutot Index (L-T ratio of arithmetic
    mean prices)"."""
    base = _column("Base")
    computed = [round(el.dutot(base, _column(c)).value * 100, 1) for c in COLUMNS]
    assert computed == DUTOT_LONG_TERM


def test_table_8_1_mean_price_rows():
    """The two mean-price rows the indices are ratios of.

    March's geometric mean is the one cell where the printed prices and
    the manual's own figure disagree (4.80 against a published 4.81), for
    the rounding reason set out in this module's docstring. Asserted as
    the value the published prices actually give, with the published
    figure named, rather than quietly excluded.
    """
    geometric = [round(float(np.exp(np.log(PRICES[c]).mean())), 2) for c in COLUMNS]
    arithmetic = [round(float(PRICES[c].mean()), 2) for c in COLUMNS]

    assert arithmetic == ARITHMETIC_MEAN_PRICES

    march = COLUMNS.index("Mar")
    assert geometric[march] == 4.80          # manual prints 4.81; see module docstring
    expected_geometric = list(GEOMETRIC_MEAN_PRICES)
    expected_geometric[march] = 4.80
    assert geometric == expected_geometric


# ---------------------------------------------------------------------
# Table 8.2: Carli on averages of long-term price relatives
# ---------------------------------------------------------------------
def test_table_8_2_carli_long_term_index():
    """CPI Manual 2020, Table 8.2, "Carli Index (L-T arithmetic
    changes)"."""
    base = _column("Base")
    computed = [round(el.carli(base, _column(c)).value * 100, 1) for c in COLUMNS]
    assert computed == CARLI_LONG_TERM


def test_table_8_2_carli_exceeds_jevons_at_every_published_month():
    """The manual's own point about these two rows, asserted directly:
    the Carli index is at or above the Jevons index in every month, which
    is paragraph 8.85's arithmetic >= geometric ranking showing up in
    published figures rather than in theory."""
    for carli_value, jevons_value in zip(CARLI_LONG_TERM, JEVONS_LONG_TERM, strict=True):
        assert carli_value >= jevons_value


# ---------------------------------------------------------------------
# Table 8.3: chained short-term relatives, and Carli's intransitivity
# ---------------------------------------------------------------------
def _chained(formula) -> list[float]:
    levels, level = [], 1.0
    for previous, current in zip(COLUMNS[:-1], COLUMNS[1:], strict=True):
        level *= formula(_column(previous), _column(current)).value
        levels.append(round(level * 100, 1))
    return levels


def test_table_8_3_chained_jevons_equals_its_direct_counterpart():
    """CPI Manual 2020, Table 8.3, "Jevons Index (chained S-T geometric
    changes)". Jevons is transitive, so this row is identical to the
    direct row in Table 8.1 -- which the test asserts as well, since that
    identity is the property the row exists to show (paragraph 8.18)."""
    assert _chained(el.jevons) == JEVONS_CHAINED
    assert _chained(el.jevons) == JEVONS_LONG_TERM[1:]


def test_table_8_3_chained_carli_drifts_and_ends_at_106_7():
    """CPI Manual 2020, Table 8.3, "Carli Index (chained S-T arithmetic
    changes)".

    The manual's demonstration of why the chained Carli "should be
    avoided" (paragraph 8.19): July's prices are exactly the base
    period's, every price having returned to where it started, and the
    chained Carli nonetheless reports 106.7 -- 6.7 percent of inflation
    that did not happen. The direct Carli for the same month reads 100.0.
    """
    assert _chained(el.carli) == CARLI_CHAINED
    assert _chained(el.carli)[-1] == 106.7
    assert CARLI_LONG_TERM[-1] == 100.0
    assert (PRICES["Jul"] == PRICES["Base"]).all()


def test_table_8_3_chained_dutot_is_also_transitive():
    """Dutot chains to its direct counterpart too, so the intransitivity
    above is specific to Carli rather than a property of chaining."""
    direct = [round(el.dutot(_column("Base"), _column(c)).value * 100, 1)
              for c in COLUMNS[1:]]
    assert _chained(el.dutot) == direct


# ---------------------------------------------------------------------
# What is deliberately not asserted
# ---------------------------------------------------------------------
def test_superlative_indices_against_a_hand_calculated_two_good_example():
    """Two goods, two periods, every number chosen so the arithmetic can
    be followed on paper. Replaces the earlier skip: the CPI Manual 2020
    prints no reproducible superlative worked example (Chapter 8 defines
    Fisher, Tornqvist and Walsh at paragraph 8.91 without one, and Table
    10.1's data grid is not machine-readable), so the proof is built here
    from the definitions rather than borrowed from a number nobody
    published.

    Prices and quantities (good A, good B):

        period 0:  p = (2, 5)    q = (10, 4)
        period t:  p = (3, 4)    q = ( 8, 6)

    A rose 50% and B fell 20%; buyers shifted from A to B, the substitution
    the superlative formulae are meant to credit.

    Laspeyres  = sum(p_t q_0) / sum(p_0 q_0)
               = (3*10 + 4*4) / (2*10 + 5*4) = (30 + 16) / (20 + 20) = 46/40 = 1.15
    Paasche    = sum(p_t q_t) / sum(p_0 q_t)
               = (3*8 + 4*6) / (2*8 + 5*6)   = (24 + 24) / (16 + 30) = 48/46 = 1.0434782609
    Fisher     = sqrt(1.15 * 48/46) = sqrt(1.2) = 1.0954451150

    Expenditure shares:
        period 0: e = (20, 20), total 40 -> s_0 = (0.5, 0.5)
        period t: e = (24, 24), total 48 -> s_t = (0.5, 0.5)
    Tornqvist  = prod (p_t/p_0)^((s_0+s_t)/2) = 1.5^0.5 * 0.8^0.5 = sqrt(1.2) = 1.0954451150
      (with equal shares in both periods, Tornqvist equals Fisher here
      exactly, which is itself a check: sqrt(46/40 * 48/46) = sqrt(48/40)
      = sqrt(1.2).)

    Walsh      = sum(p_t sqrt(q_0 q_t)) / sum(p_0 sqrt(q_0 q_t))
        sqrt(10*8) = sqrt(80) = 8.9442719, sqrt(4*6) = sqrt(24) = 4.8989795
               = (3*8.9442719 + 4*4.8989795) / (2*8.9442719 + 5*4.8989795)
               = (26.8328157 + 19.5959179) / (17.8885438 + 24.4948974)
               = 46.4287336 / 42.3834412 = 1.0954451150   (also sqrt(1.2): the
        two goods' quantities scale by 0.8 and 1.5 = the inverse of their
        price relatives, the unit-elastic case where every superlative
        formula agrees)

    Marshall-Edgeworth = sum(p_t (q_0+q_t)) / sum(p_0 (q_0+q_t))
               = (3*18 + 4*10) / (2*18 + 5*10) = (54 + 40) / (36 + 50) = 94/86 = 1.0930232558

    Fisher quantity = sqrt( sum(q_t p_0)/sum(q_0 p_0) * sum(q_t p_t)/sum(q_0 p_t) )
               = sqrt( 46/40 * 48/46 ) = sqrt(1.2)
    Value ratio = 48/40 = 1.2 = Fisher price x Fisher quantity = 1.2   (factor reversal)
    """
    p0 = pd.Series([2.0, 5.0], index=["A", "B"])
    pt = pd.Series([3.0, 4.0], index=["A", "B"])
    q0 = pd.Series([10.0, 4.0], index=["A", "B"])
    qt = pd.Series([8.0, 6.0], index=["A", "B"])

    assert bi.laspeyres(p0, pt, q0).value == pytest.approx(1.15, abs=1e-6)
    assert bi.paasche(p0, pt, qt).value == pytest.approx(48 / 46, abs=1e-6)
    assert bi.fisher(p0, pt, q0, qt).value == pytest.approx(1.0954451150, abs=1e-6)
    assert bi.tornqvist(p0, pt, q0, qt).value == pytest.approx(1.0954451150, abs=1e-6)
    assert bi.walsh(p0, pt, q0, qt).value == pytest.approx(1.0954451150, abs=1e-6)
    assert bi.marshall_edgeworth(p0, pt, q0, qt).value == pytest.approx(94 / 86, abs=1e-6)
    assert bi.fisher_quantity(p0, pt, q0, qt).value == pytest.approx(1.0954451150, abs=1e-6)
    assert bi.value_ratio(p0, pt, q0, qt) == pytest.approx(1.2, abs=1e-6)
    assert bi.fisher(p0, pt, q0, qt).value * bi.fisher_quantity(p0, pt, q0, qt).value ==         pytest.approx(1.2, abs=1e-6)
    legs = bi.fisher(p0, pt, q0, qt).components
    assert legs["laspeyres"] == pytest.approx(1.15, abs=1e-6)
    assert legs["paasche"] == pytest.approx(48 / 46, abs=1e-6)


def test_superlative_indices_on_a_second_hand_example_where_they_differ():
    """The first example is the unit-elastic case where every superlative
    formula coincides, which proves the arithmetic but not that the
    formulae are distinct. A second example with unequal shares:

        period 0:  p = (1, 2)    q = (6, 3)      e = (6, 6),  s_0 = (0.5, 0.5)
        period t:  p = (2, 2)    q = (2, 5)      e = (4, 10), s_t = (2/7, 5/7)

    Laspeyres  = (2*6 + 2*3) / (1*6 + 2*3) = 18/12 = 1.5
    Paasche    = (2*2 + 2*5) / (1*2 + 2*5) = 14/12 = 1.1666666667
    Fisher     = sqrt(1.5 * 14/12) = sqrt(1.75) = 1.3228756555
    Tornqvist  = 2^((0.5 + 2/7)/2) * 1^((0.5 + 5/7)/2) = 2^(11/28) = 1.3130352855
    Walsh      = (2*sqrt(12) + 2*sqrt(15)) / (1*sqrt(12) + 2*sqrt(15))
               = (6.9282032 + 7.7459667) / (3.4641016 + 7.7459667)
               = 14.6741699 / 11.2100683 = 1.3090170
    """
    p0 = pd.Series([1.0, 2.0], index=["A", "B"])
    pt = pd.Series([2.0, 2.0], index=["A", "B"])
    q0 = pd.Series([6.0, 3.0], index=["A", "B"])
    qt = pd.Series([2.0, 5.0], index=["A", "B"])
    assert bi.laspeyres(p0, pt, q0).value == pytest.approx(1.5, abs=1e-9)
    assert bi.paasche(p0, pt, qt).value == pytest.approx(14 / 12, abs=1e-9)
    assert bi.fisher(p0, pt, q0, qt).value == pytest.approx(1.3228756555, abs=1e-9)
    assert bi.tornqvist(p0, pt, q0, qt).value == pytest.approx(2 ** (11 / 28), abs=1e-9)
    assert bi.walsh(p0, pt, q0, qt).value == pytest.approx(1.3090170, abs=1e-6)
    assert bi.fisher(p0, pt, q0, qt).value != pytest.approx(bi.tornqvist(p0, pt, q0, qt).value)
