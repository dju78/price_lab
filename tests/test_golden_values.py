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
def test_no_superlative_golden_values_are_asserted():
    """A placeholder that records a negative finding rather than leaving a
    silent gap in the coverage.

    The phase asked for golden values from the CPI Manual 2020 chapters on
    elementary aggregation *and* superlative indices. The elementary ones
    are above. The superlative ones are not, because the 2020 volume does
    not contain a reproducible worked example of one:

      - Chapter 8 defines Fisher, Tornqvist and Walsh (paragraph 8.91) but
        prints no numerical example of any of them.
      - Every table in the volume that names Fisher, Walsh or Tornqvist
        (Tables 8.10, 9.1 and 10.1) was checked. Table 10.1 is the closest
        -- a two-item, nine-period chained Tornqvist ending at 78.18 --
        but its data grid is not machine-readable in the published PDF and
        the surrounding prose gives the discounted prices only in words
        and the quantities not at all, so the 78.18 cannot be reproduced
        from what is printed.
      - The volume defers index theory to a companion, published in 2025
        as Consumer Price Index Manual: Theory. Its worked examples run on
        large empirical data sets (scanner data, Israeli fresh fruit) that
        are summarised rather than printed, so they are not reproducible
        either.

    Rather than invent expected values, the superlative formulae are
    verified by the exact algebraic identities in `tests/test_axioms.py`:
    factor reversal for Fisher, time reversal for Fisher and Tornqvist,
    the Laspeyres/Fisher/Paasche ordering under substitution, and
    Tornqvist as the geometric mean of the geometric Laspeyres and
    geometric Paasche. Those verify the same arithmetic without borrowing
    authority from a number nobody published.
    """
    pytest.skip(
        "no reproducible superlative worked example exists in the CPI Manual 2020; "
        "see this test's docstring, and tests/test_axioms.py for the algebraic "
        "identities used instead")
