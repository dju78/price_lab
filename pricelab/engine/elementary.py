"""Elementary aggregate indices: the unweighted formulae applied below the
level at which expenditure weights exist.

Every function here returns a `ElementaryResult` rather than a bare float,
because at this level the number on its own is not reportable: an index of
103.2 computed over four matched items, two of them imputed, is a
different claim from the same 103.2 computed over forty observed ones, and
the difference has to travel with the value rather than being reconstructed
later from somewhere else.

Jevons, Dutot and Carli are not reimplemented here. `engine.index` already
holds them, they are on the hot path of every run, and they are what the
existing test suite pins; this module calls those implementations and adds
the sample-size, imputation and provenance record around them. The three
formulae added here -- the harmonic mean of price relatives, CSWD, and the
unit value index -- had no implementation anywhere.

Bias direction is stated in every docstring because at the elementary level
the choice of formula *is* the methodological argument, and the CPI Manual
2020 (paragraph 8.85) is explicit that the ranking arithmetic >= geometric
>= harmonic always holds: Carli >= Jevons >= harmonic mean, with equality
only when every price relative is identical. Choosing one is choosing a
direction of bias, so each docstring names which.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .index import _matched
from .index import carli as _carli_value
from .index import dutot as _dutot_value
from .index import jevons as _jevons_value


class HomogeneityNotAssertedError(ValueError):
    """A unit value index was requested without the caller asserting that
    the items being averaged are homogeneous."""


@dataclass(frozen=True)
class ElementaryResult:
    """One elementary index value and everything needed to read it."""

    value: float
    """The index as a ratio, not a percentage: 1.032 means a 3.2% rise."""
    n_items: int
    """Matched items that actually contributed. Items priced in only one of
    the two periods are excluded before any formula runs, so this is
    generally smaller than the number of items in the sample."""
    n_imputed: int
    """How many of those `n_items` carried an imputed rather than an
    observed current-period price. Reported separately rather than folded
    into `n_items` because an index resting mostly on imputation is a
    weaker claim than the same index resting on collection, and the
    reader cannot tell the two apart from the value."""
    formula: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    """The formula's own settings, recorded so the value can be
    reproduced: for `unit_value`, the homogeneity assertion that licensed
    it; empty for the formulae that take no parameters."""

    @property
    def imputed_share(self) -> float:
        return self.n_imputed / self.n_items if self.n_items else float("nan")


def _contributors(
    a: pd.Series, b: pd.Series, imputed: pd.Series | None
) -> tuple[pd.Series, pd.Series, int, int]:
    """Matched items and the imputation count among them.

    Matching is `engine.index._matched`, the same rule the rest of the
    engine applies, so an elementary result's sample size describes the
    same set of items the index value was computed from rather than a
    separately-derived count that could disagree with it.
    """
    am, bm = _matched(a, b)
    n_items = int(len(am))
    if imputed is None or n_items == 0:
        return am, bm, n_items, 0
    flags = imputed.reindex(am.index).fillna(False).astype(bool)
    return am, bm, n_items, int(flags.sum())


def jevons(a: pd.Series, b: pd.Series, imputed: pd.Series | None = None) -> ElementaryResult:
    """Unweighted geometric mean of price relatives.

        P = prod(p_t_i / p_0_i) ^ (1/n)

    CPI Manual 2020, Chapter 8, equation (8.1). Identical to the ratio of
    unweighted geometric mean prices, which is why it is transitive: a
    chained Jevons equals the direct Jevons exactly (paragraph 8.18).

    Bias: none of the elementary formulae is unbiased against a
    cost-of-living index, but Jevons is the one whose implicit assumption
    -- unit elasticity of substitution -- is the closest to plausible for
    an elementary aggregate of close substitutes (paragraph 8.86). It sits
    below Carli and above the harmonic mean by construction.
    """
    am, bm, n_items, n_imputed = _contributors(a, b, imputed)
    return ElementaryResult(_jevons_value(am, bm), n_items, n_imputed, "jevons")


def dutot(a: pd.Series, b: pd.Series, imputed: pd.Series | None = None) -> ElementaryResult:
    """Ratio of unweighted arithmetic mean prices.

        P = (sum p_t_i / n) / (sum p_0_i / n)

    CPI Manual 2020, Chapter 8, equation (8.3). Transitive, like Jevons.

    Bias: fails the commensurability test -- changing the unit one item is
    quantified in changes the index, because the mean price is dominated by
    whichever items happen to be expensive in the chosen units. Safe only
    over a genuinely homogeneous set of comparable items, and misleading
    the moment the aggregate mixes items priced per kilo with items priced
    per gram.
    """
    am, bm, n_items, n_imputed = _contributors(a, b, imputed)
    return ElementaryResult(_dutot_value(am, bm), n_items, n_imputed, "dutot")


def carli(a: pd.Series, b: pd.Series, imputed: pd.Series | None = None) -> ElementaryResult:
    """Unweighted arithmetic mean of price relatives.

        P = (1/n) * sum(p_t_i / p_0_i)

    CPI Manual 2020, Chapter 8, equation (8.5).

    Bias: upward, and not a small one. Carli fails time reversal, so it is
    intransitive: chaining it accumulates the bias with every link. The
    manual's own worked example (Table 8.3) chains Carli across seven
    months whose prices end exactly where they started and finishes at
    106.7 -- a fabricated 6.7 percent inflation from data containing none.
    Implemented so that bias can be demonstrated, not recommended;
    paragraph 8.19 says the chained Carli "should be avoided."
    """
    am, bm, n_items, n_imputed = _contributors(a, b, imputed)
    return ElementaryResult(_carli_value(am, bm), n_items, n_imputed, "carli")


def harmonic_mean(a: pd.Series, b: pd.Series, imputed: pd.Series | None = None) -> ElementaryResult:
    """Unweighted harmonic mean of price relatives.

        P = n / sum(p_0_i / p_t_i)

    CPI Manual 2020, Chapter 8, equation (8.10).

    Bias: downward, and it is Carli's mirror image -- the harmonic mean of
    the relatives is the time antithesis of Carli, so it fails time
    reversal by exactly the amount Carli does, in the opposite direction.
    That symmetry is the whole reason CSWD (below) works. Rarely used
    directly: paragraph 8.84 notes the harmonic mean "is not a familiar
    concept and would not be easy to explain to users", though the Paasche
    index is one at the weighted level.
    """
    am, bm, n_items, n_imputed = _contributors(a, b, imputed)
    if n_items == 0:
        return ElementaryResult(float("nan"), 0, 0, "harmonic_mean")
    value = float(n_items / (am.to_numpy() / bm.to_numpy()).sum())
    return ElementaryResult(value, n_items, n_imputed, "harmonic_mean")


def cswd(a: pd.Series, b: pd.Series, imputed: pd.Series | None = None) -> ElementaryResult:
    """Carruthers-Sellwood-Ward-Dalen: the geometric mean of the Carli and
    harmonic-mean indices.

        P = sqrt(P_carli * P_harmonic)

    Carruthers, Sellwood and Ward (1980) and Dalen (1992), both cited in
    the CPI Manual 2020's bibliography; discussed in Chapter 6 of the
    companion volume Consumer Price Index Theory.

    Bias: near-cancelling. Carli's upward bias and the harmonic mean's
    downward bias are time antitheses of each other, so taking the
    geometric mean of the pair produces an index that satisfies time
    reversal exactly and tracks Jevons closely -- it is a very good
    approximation to Jevons in practice, and equals it to second order.
    Included because it is what an office already computing Carli can
    move to without also having to explain geometric means.
    """
    am, bm, n_items, n_imputed = _contributors(a, b, imputed)
    if n_items == 0:
        return ElementaryResult(float("nan"), 0, 0, "cswd")
    relatives = bm.to_numpy() / am.to_numpy()
    carli_value = float(relatives.mean())
    harmonic_value = float(n_items / (1.0 / relatives).sum())
    return ElementaryResult(
        float(np.sqrt(carli_value * harmonic_value)), n_items, n_imputed, "cswd")


def unit_value(
    a: pd.Series,
    b: pd.Series,
    qa: pd.Series,
    qb: pd.Series,
    *,
    homogeneous: bool = False,
    homogeneity_justification: str = "",
    imputed: pd.Series | None = None,
) -> ElementaryResult:
    """Ratio of unit values: total expenditure divided by total quantity,
    in the current period over the reference period.

        P = (sum p_t_i q_t_i / sum q_t_i) / (sum p_0_i q_0_i / sum q_0_i)

    CPI Manual 2020, Chapter 8, paragraph 8.87.

    Bias: none, *if* the items really are one homogeneous product -- in
    that case, as paragraph 8.34 puts it, there is no index number problem
    at all and the unit value ratio is the price index. Applied to a set
    that is not homogeneous it is not biased so much as meaningless: it
    silently reports a shift in the *mix* of what was bought as a change in
    price. Sell the same two goods at unchanged prices but switch the
    quantities from mostly-cheap to mostly-dear and this index rises,
    having measured nothing.

    That failure is invisible in the output, which is why homogeneity must
    be asserted explicitly by the caller (`homogeneous=True` plus a stated
    justification) rather than assumed by a default. The assertion is
    recorded in `parameters` so a reviewer can see who claimed it and on
    what grounds. Quantities "must be strictly additive in an economic
    sense" (paragraph 8.87): that is a judgement about the products, and
    nothing in the data can make it for you.
    """
    if not homogeneous:
        raise HomogeneityNotAssertedError(
            "unit_value is only defined over a set of strictly homogeneous items, whose "
            "quantities are additive in an economic sense (CPI Manual 2020, 8.87). Applied "
            "to a mixed set it reports a change in purchase mix as a change in price, and "
            "nothing in the output would show that it had. Pass homogeneous=True with a "
            "homogeneity_justification if that condition genuinely holds; use jevons() if "
            "it does not.")
    if not homogeneity_justification.strip():
        raise HomogeneityNotAssertedError(
            "homogeneous=True must come with a homogeneity_justification: the assertion is "
            "recorded with the result so a reviewer can judge it, and an unexplained "
            "assertion is not reviewable.")

    am, bm, n_items, n_imputed = _contributors(a, b, imputed)
    parameters = {"homogeneous": True, "homogeneity_justification": homogeneity_justification}
    if n_items == 0:
        return ElementaryResult(float("nan"), 0, 0, "unit_value", parameters)

    qam = qa.reindex(am.index).astype(float)
    qbm = qb.reindex(bm.index).astype(float)
    qa_sum, qb_sum = float(qam.sum()), float(qbm.sum())
    if not (qa_sum > 0 and qb_sum > 0):
        return ElementaryResult(float("nan"), n_items, n_imputed, "unit_value", parameters)

    uv_a = float((am.astype(float) * qam).sum()) / qa_sum
    uv_b = float((bm.astype(float) * qbm).sum()) / qb_sum
    value = uv_b / uv_a if uv_a > 0 else float("nan")
    return ElementaryResult(value, n_items, n_imputed, "unit_value", parameters)


#: Every elementary formula that needs only prices, by name. `unit_value`
#: is deliberately absent: it needs quantities and an explicit homogeneity
#: assertion, so it cannot be selected by name from a dropdown the way
#: these can, which is the point.
ELEMENTARY_FORMULAE = {
    "jevons": jevons,
    "dutot": dutot,
    "carli": carli,
    "harmonic_mean": harmonic_mean,
    "cswd": cswd,
}
