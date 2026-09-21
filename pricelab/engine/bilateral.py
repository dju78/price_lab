"""Bilateral aggregate indices: the weighted formulae that compare two
periods using quantities or expenditure shares.

Three families, and the difference between them is which basket is held
fixed:

fixed basket        Laspeyres (base quantities), Paasche (current
                    quantities), Lowe (quantities from a third, earlier
                    weight reference period).
symmetric averages  Fisher, Walsh, Marshall-Edgeworth, Tornqvist. Each
                    answers Laspeyres' and Paasche's disagreement by
                    averaging the two baskets rather than choosing one.
                    Fisher and Tornqvist are superlative.
share-weighted      Young (expenditure shares from the weight reference
                    period), geometric Laspeyres and geometric Paasche.

`engine.index.laspeyres` is untouched and still does what it always did,
including its fall back to Jevons when no weights are supplied: every run
compiled through the interface today goes through that function, and
changing it would change already-published numbers. Note that what it
computes is a weighted arithmetic mean of price relatives -- the Young
form -- rather than the quantity-basket form below. Both are called
"Laspeyres" in practice; they coincide when the weights are the base
period's own expenditure shares, and differ when they are not, which is
exactly the distinction this module exists to make explicit.

Why weight_reference_period finally matters
-------------------------------------------
Lowe and Young are the forms most statistical offices actually publish,
because expenditure data always lags: the weights come from a household
budget survey covering some period b, and the index starts from a price
reference period 0 that is later than b. That gap is the whole reason
`IndexConfig` carries three reference periods rather than one. Lowe holds
the *quantities* of period b fixed and prices them at 0 and t; Young holds
the *expenditure shares* of period b fixed and applies them to price
relatives from 0 to t. They are the same index when no prices moved
between b and 0, and they diverge as soon as any did -- the divergence
being precisely the effect of price-updating the weights, which
`price_updating_effect` reports separately rather than leaving as an
unexplained gap between two numbers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

QuantityLike = pd.Series | None


@dataclass(frozen=True)
class BilateralResult:
    value: float
    """The index as a ratio: 1.032 is a 3.2% rise."""
    n_items: int
    """Items priced and quantified in every period the formula needs."""
    n_imputed: int
    formula: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    components: Mapping[str, float] = field(default_factory=dict)
    """Intermediate values worth reading on their own: the Laspeyres and
    Paasche legs behind a Fisher, the price-updated weight sum behind a
    Lowe. A symmetric average that sits between two wildly disagreeing
    legs is a different object from one whose legs nearly coincide, and
    the averaged value alone does not say which it is."""

    @property
    def imputed_share(self) -> float:
        return self.n_imputed / self.n_items if self.n_items else float("nan")


def _aligned(
    p0: pd.Series, pt: pd.Series, *quantities: QuantityLike
) -> tuple[np.ndarray, np.ndarray, list[np.ndarray], pd.Index]:
    """Common items, positive in both price vectors and finite in every
    supplied quantity vector.

    Matching before weighting, for the same reason the elementary
    formulae match before averaging: an item priced in only one of the two
    periods carries no price relative, and including it would score the
    item's arrival or departure as price movement.
    """
    index = p0.index
    for other in (pt, *[q for q in quantities if q is not None]):
        index = index.intersection(other.index)

    a = p0.reindex(index).to_numpy(dtype=float)
    b = pt.reindex(index).to_numpy(dtype=float)
    valid = np.isfinite(a) & np.isfinite(b) & (a > 0) & (b > 0)

    aligned_q = []
    for q in quantities:
        if q is None:
            aligned_q.append(np.empty(0))
            continue
        arr = q.reindex(index).to_numpy(dtype=float)
        valid &= np.isfinite(arr)
        aligned_q.append(arr)

    return a[valid], b[valid], [q[valid] if q.size else q for q in aligned_q], index[valid]


def _imputed_count(imputed: pd.Series | None, index: pd.Index) -> int:
    if imputed is None or len(index) == 0:
        return 0
    return int(imputed.reindex(index).fillna(False).astype(bool).sum())


def _empty(formula: str, parameters: Mapping[str, Any] | None = None) -> BilateralResult:
    return BilateralResult(float("nan"), 0, 0, formula, parameters or {})


# ---------------------------------------------------------------------
# Fixed basket
# ---------------------------------------------------------------------
def laspeyres(
    p0: pd.Series, pt: pd.Series, q0: pd.Series, imputed: pd.Series | None = None
) -> BilateralResult:
    """Base-period basket, priced at both periods.

        P_L = sum(p_t q_0) / sum(p_0 q_0)

    CPI Manual 2020, Chapter 8, paragraphs 8.89-8.95.

    Bias: upward against a cost-of-living index. It holds quantities at
    their base-period levels, so it gives no credit to the substitution
    consumers actually make away from whatever became relatively dearer.
    The size of that bias is the gap between this and Paasche, quantified
    by `substitution_bias_pp`.
    """
    a, b, (qa,), index = _aligned(p0, pt, q0)
    if a.size == 0 or (a * qa).sum() <= 0:
        return _empty("laspeyres")
    value = float((b * qa).sum() / (a * qa).sum())
    return BilateralResult(value, int(a.size), _imputed_count(imputed, index), "laspeyres")


def paasche(
    p0: pd.Series, pt: pd.Series, qt: pd.Series, imputed: pd.Series | None = None
) -> BilateralResult:
    """Current-period basket, priced at both periods.

        P_P = sum(p_t q_t) / sum(p_0 q_t)

    CPI Manual 2020, Chapter 8, paragraphs 8.89-8.95.

    Bias: downward against a cost-of-living index, for the mirror-image
    reason Laspeyres is biased up -- it weights by quantities that have
    already adjusted to the new prices, crediting the full substitution.
    Also impractical as a published CPI on its own: current-period
    quantities are not known until long after the index must be published.
    """
    a, b, (qb_,), index = _aligned(p0, pt, qt)
    if a.size == 0 or (a * qb_).sum() <= 0:
        return _empty("paasche")
    value = float((b * qb_).sum() / (a * qb_).sum())
    return BilateralResult(value, int(a.size), _imputed_count(imputed, index), "paasche")


def lowe(
    p0: pd.Series, pt: pd.Series, qb: pd.Series, imputed: pd.Series | None = None
) -> BilateralResult:
    """A fixed basket of quantities from the weight reference period b,
    priced at the price reference period 0 and at t.

        P_Lo = sum(p_t q_b) / sum(p_0 q_b)

    CPI Manual 2020, Chapter 9 (equation 9.6 and paragraphs 9.57-9.59),
    and Chapter 8 paragraphs 8.100-8.104.

    This is what most offices publish and call a "Laspeyres-type" index:
    it is a Laspeyres only in the special case b = 0. It is the reason
    `weight_reference_period` exists in the config -- and, until this
    phase, the reason that field was carried but never read.

    Bias: inherits Laspeyres' upward substitution bias and adds to it. The
    longer the gap between b and 0, the more the fixed basket has drifted
    from what anyone currently buys, so the bias generally grows with that
    gap rather than with the index's own span.
    """
    a, b, (qbb,), index = _aligned(p0, pt, qb)
    if a.size == 0 or (a * qbb).sum() <= 0:
        return _empty("lowe")
    value = float((b * qbb).sum() / (a * qbb).sum())
    return BilateralResult(value, int(a.size), _imputed_count(imputed, index), "lowe")


def young(
    p0: pd.Series, pt: pd.Series, sb: pd.Series, imputed: pd.Series | None = None
) -> BilateralResult:
    """Expenditure shares from the weight reference period b, applied to
    price relatives from 0 to t.

        P_Y = sum(s_b * (p_t / p_0)) / sum(s_b)

    CPI Manual 2020, Chapter 9, paragraphs 9.37-9.38 and 9.59; Chapter 8
    paragraph 8.104.

    The alternative to Lowe when new weights arrive: introduce period b's
    expenditure shares directly rather than price-updating them. Holding
    *shares* fixed rather than *quantities* fixed is an implicit assumption
    that consumers substitute exactly enough to keep each item's budget
    share constant -- unit elasticity -- where Lowe assumes no substitution
    at all. Neither is right in general, and the manual is explicit
    (paragraph 9.59) that it cannot be said a priori which of the two will
    be higher.

    `sb` need not sum to 1; it is normalised here, so raw expenditure
    values may be passed directly.
    """
    a, b, (s,), index = _aligned(p0, pt, sb)
    if a.size == 0 or s.sum() <= 0:
        return _empty("young")
    value = float((s * (b / a)).sum() / s.sum())
    return BilateralResult(value, int(a.size), _imputed_count(imputed, index), "young")


# ---------------------------------------------------------------------
# Symmetric averages
# ---------------------------------------------------------------------
def fisher(
    p0: pd.Series, pt: pd.Series, q0: pd.Series, qt: pd.Series,
    imputed: pd.Series | None = None,
) -> BilateralResult:
    """Geometric mean of Laspeyres and Paasche.

        P_F = sqrt(P_L * P_P)

    CPI Manual 2020, Chapter 8, paragraph 8.91; superlative, and the only
    index satisfying both time reversal and factor reversal -- Fisher's
    own "ideal" index.

    Bias: the point of it is that the two legs' opposite substitution
    biases largely cancel. `components` carries both legs, because a
    Fisher sitting midway between legs that disagree by twenty points is
    a much weaker number than one whose legs nearly coincide, and the
    value alone cannot tell you which you have.
    """
    la = laspeyres(p0, pt, q0, imputed)
    pa = paasche(p0, pt, qt, imputed)
    if not (np.isfinite(la.value) and np.isfinite(pa.value)):
        return _empty("fisher")
    return BilateralResult(
        float(np.sqrt(la.value * pa.value)),
        min(la.n_items, pa.n_items), max(la.n_imputed, pa.n_imputed), "fisher",
        components={"laspeyres": la.value, "paasche": pa.value,
                    "substitution_bias_pp": (la.value - pa.value) * 100.0})


def tornqvist(
    p0: pd.Series, pt: pd.Series, q0: pd.Series, qt: pd.Series,
    imputed: pd.Series | None = None,
) -> BilateralResult:
    """Share-weighted geometric mean of price relatives, the weights being
    the average of each item's expenditure share in the two periods.

        P_T = prod( (p_t_i / p_0_i) ^ ((s_0_i + s_t_i) / 2) )

    CPI Manual 2020, Chapter 8, paragraph 8.91; superlative, and in
    practice indistinguishable from Fisher (Chapter 9, paragraph 9.9,
    notes Fisher, Tornqvist and Walsh "produce essentially the same
    results in practice").

    Bias: negligible against a cost-of-living index -- it is exact for a
    translog preference structure, which is a second-order approximation
    to any well-behaved one. Satisfies time reversal, fails factor
    reversal (only Fisher passes both).
    """
    a, b, (qa, qb_), index = _aligned(p0, pt, q0, qt)
    if a.size == 0:
        return _empty("tornqvist")
    e0, et = a * qa, b * qb_
    if e0.sum() <= 0 or et.sum() <= 0:
        return _empty("tornqvist")
    s0, st = e0 / e0.sum(), et / et.sum()
    weights = (s0 + st) / 2.0
    value = float(np.exp((weights * np.log(b / a)).sum()))
    return BilateralResult(value, int(a.size), _imputed_count(imputed, index), "tornqvist")


def walsh(
    p0: pd.Series, pt: pd.Series, q0: pd.Series, qt: pd.Series,
    imputed: pd.Series | None = None,
) -> BilateralResult:
    """Fixed basket of the geometric mean of the two periods' quantities.

        P_W = sum(p_t * sqrt(q_0 q_t)) / sum(p_0 * sqrt(q_0 q_t))

    CPI Manual 2020, Chapter 8, paragraph 8.91; superlative.

    Bias: small and symmetric, like Fisher's. Of the symmetric baskets it
    is the one that best satisfies the "pure price index" idea -- the
    basket it prices is a real, representative basket sitting between the
    two periods, rather than an arithmetic compromise between two
    separately-computed answers.
    """
    a, b, (qa, qb_), index = _aligned(p0, pt, q0, qt)
    if a.size == 0:
        return _empty("walsh")
    basket = np.sqrt(qa * qb_)
    if (a * basket).sum() <= 0:
        return _empty("walsh")
    value = float((b * basket).sum() / (a * basket).sum())
    return BilateralResult(value, int(a.size), _imputed_count(imputed, index), "walsh")


def marshall_edgeworth(
    p0: pd.Series, pt: pd.Series, q0: pd.Series, qt: pd.Series,
    imputed: pd.Series | None = None,
) -> BilateralResult:
    """Fixed basket of the arithmetic mean of the two periods' quantities.

        P_ME = sum(p_t * (q_0 + q_t)/2) / sum(p_0 * (q_0 + q_t)/2)

    CPI Manual 2020, Chapter 8, paragraph 8.91.

    Bias: small, and it satisfies time reversal. Not superlative, and it
    has one practical weakness Walsh does not: the arithmetic mean basket
    is dominated by whichever period had the larger quantities, so a
    product whose volume collapses between the two periods still carries
    close to half its original weight.
    """
    a, b, (qa, qb_), index = _aligned(p0, pt, q0, qt)
    if a.size == 0:
        return _empty("marshall_edgeworth")
    basket = (qa + qb_) / 2.0
    if (a * basket).sum() <= 0:
        return _empty("marshall_edgeworth")
    value = float((b * basket).sum() / (a * basket).sum())
    return BilateralResult(value, int(a.size), _imputed_count(imputed, index), "marshall_edgeworth")


# ---------------------------------------------------------------------
# Geometric counterparts
# ---------------------------------------------------------------------
def geometric_laspeyres(
    p0: pd.Series, pt: pd.Series, q0: pd.Series, imputed: pd.Series | None = None
) -> BilateralResult:
    """Base-share-weighted geometric mean of price relatives.

        P_GL = prod( (p_t_i / p_0_i) ^ s_0_i )

    CPI Manual 2020, Chapter 8, paragraph 8.99 (the weighted geometric
    mean of price relatives).

    Bias: below the arithmetic Laspeyres always, by the same
    arithmetic >= geometric ranking that puts Jevons below Carli. Its
    implicit assumption is unit elasticity of substitution, where the
    arithmetic Laspeyres assumes zero, so it is the Cobb-Douglas
    counterpart of the fixed basket.
    """
    a, b, (qa,), index = _aligned(p0, pt, q0)
    if a.size == 0:
        return _empty("geometric_laspeyres")
    e0 = a * qa
    if e0.sum() <= 0:
        return _empty("geometric_laspeyres")
    s0 = e0 / e0.sum()
    value = float(np.exp((s0 * np.log(b / a)).sum()))
    return BilateralResult(value, int(a.size), _imputed_count(imputed, index),
                           "geometric_laspeyres")


def geometric_paasche(
    p0: pd.Series, pt: pd.Series, qt: pd.Series, imputed: pd.Series | None = None
) -> BilateralResult:
    """Current-share-weighted geometric mean of price relatives.

        P_GP = prod( (p_t_i / p_0_i) ^ s_t_i )

    CPI Manual 2020, Chapter 8, paragraph 8.99.

    Bias: the mirror of geometric Laspeyres, and the geometric mean of the
    two is the Tornqvist index -- which is why Tornqvist is the geometric
    family's answer to Fisher.
    """
    a, b, (qb_,), index = _aligned(p0, pt, qt)
    if a.size == 0:
        return _empty("geometric_paasche")
    et = b * qb_
    if et.sum() <= 0:
        return _empty("geometric_paasche")
    st = et / et.sum()
    value = float(np.exp((st * np.log(b / a)).sum()))
    return BilateralResult(value, int(a.size), _imputed_count(imputed, index),
                           "geometric_paasche")


# ---------------------------------------------------------------------
# Quantity indices, for the factor reversal test
# ---------------------------------------------------------------------
def fisher_quantity(
    p0: pd.Series, pt: pd.Series, q0: pd.Series, qt: pd.Series
) -> BilateralResult:
    """The Fisher quantity index: Fisher's price formula with the roles of
    price and quantity exchanged.

        Q_F = sqrt( (sum q_t p_0 / sum q_0 p_0) * (sum q_t p_t / sum q_0 p_t) )

    Exists so factor reversal can actually be tested rather than asserted:
    the Fisher price index multiplied by this must equal the ratio of
    total expenditure between the two periods, exactly. CPI Manual 2020,
    Chapter 8, paragraph 8.91.
    """
    a, b, (qa, qb_), _index = _aligned(p0, pt, q0, qt)
    if a.size == 0:
        return _empty("fisher_quantity")
    lq_den, pq_den = (qa * a).sum(), (qa * b).sum()
    if lq_den <= 0 or pq_den <= 0:
        return _empty("fisher_quantity")
    laspeyres_q = (qb_ * a).sum() / lq_den
    paasche_q = (qb_ * b).sum() / pq_den
    return BilateralResult(
        float(np.sqrt(laspeyres_q * paasche_q)), int(a.size), 0, "fisher_quantity",
        components={"laspeyres_quantity": float(laspeyres_q),
                    "paasche_quantity": float(paasche_q)})


def value_ratio(p0: pd.Series, pt: pd.Series, q0: pd.Series, qt: pd.Series) -> float:
    """Total expenditure at t over total expenditure at 0, over the
    matched set. The quantity factor reversal must decompose into."""
    a, b, (qa, qb_), _index = _aligned(p0, pt, q0, qt)
    if a.size == 0 or (a * qa).sum() <= 0:
        return float("nan")
    return float((b * qb_).sum() / (a * qa).sum())


# ---------------------------------------------------------------------
# Price updating: the difference between Lowe and Young
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class PriceUpdateReport:
    """What price-updating the weights did, stated rather than buried."""

    lowe: float
    """Index with quantities of period b held fixed (weights price-updated
    from b to 0)."""
    young: float
    """Index with expenditure shares of period b held fixed (weights used
    as collected)."""
    difference_pp: float
    """lowe - young, in percentage points of index level. This is the
    entire effect of price updating: the two formulae are otherwise the
    same weighted mean over the same relatives."""
    weight_price_change: float
    """The aggregate price change between the weight reference period b
    and the price reference period 0 that drives the update. When this is
    1.0 -- no price movement between b and 0 -- Lowe and Young coincide."""
    updated_shares: pd.Series
    """Period b's expenditure shares carried forward to period 0 prices."""


def price_update_shares(sb: pd.Series, pb: pd.Series, p0: pd.Series) -> pd.Series:
    """Carry expenditure shares from the weight reference period b to the
    price reference period 0, holding quantities fixed.

        s_0_i = s_b_i * (p_0_i / p_b_i) / sum_j( s_b_j * (p_0_j / p_b_j) )

    CPI Manual 2020, Chapter 9, paragraphs 9.37-9.38 and Table 9.2.

    This is the step that turns a Young index into a Lowe index. Holding
    quantities fixed while prices move means expenditure shares must move:
    an item whose price rose faster than average takes a larger share of
    the same basket. Skipping the step (the Young index) instead holds the
    shares still and lets the implied quantities move inversely to price.
    """
    ratio = (p0 / pb).replace([np.inf, -np.inf], np.nan)
    updated = sb * ratio
    total = updated.sum()
    if not np.isfinite(total) or total <= 0:
        # Refuse rather than fall back to the un-updated shares. The
        # fallback would hand `price_updating_effect` a "Lowe" that is
        # really a Young, and it would then report a price-updating effect
        # of exactly zero -- a confident answer to a question the data
        # could not answer.
        raise ValueError(
            "cannot price-update the weights: no item has a finite, positive price in both "
            "the weight reference period and the price reference period, so the update "
            "ratio p_0 / p_b is undefined for every item. A Lowe index needs those prices; "
            "use young() if only the weight-period shares are usable.")
    return updated / total


def price_updating_effect(
    p0: pd.Series, pt: pd.Series, pb: pd.Series, sb: pd.Series
) -> PriceUpdateReport:
    """Compute the Lowe and Young indices over the same data and attribute
    the gap between them to price updating.

    The two indices differ in exactly one respect -- whether period b's
    weights were carried forward to period 0 prices before being applied
    -- so the difference between them *is* the effect of that choice.
    Reporting it as its own number is the point: an office that switched
    from Young to Lowe and saw its headline move needs to be able to say
    how much of that move was the new weights and how much was the price
    updating, and "the formula changed" is not an answer anyone can audit.
    """
    updated = price_update_shares(sb, pb, p0)
    lowe_result = young(p0, pt, updated)          # Lowe == Young on price-updated shares
    young_result = young(p0, pt, sb)

    common = sb.index.intersection(pb.index).intersection(p0.index)
    s = sb.reindex(common).to_numpy(dtype=float)
    ratio = (p0.reindex(common) / pb.reindex(common)).to_numpy(dtype=float)
    finite = np.isfinite(s) & np.isfinite(ratio)
    weight_change = float((s[finite] * ratio[finite]).sum() / s[finite].sum()) if finite.any() \
        else float("nan")

    return PriceUpdateReport(
        lowe=lowe_result.value,
        young=young_result.value,
        difference_pp=(lowe_result.value - young_result.value) * 100.0,
        weight_price_change=weight_change,
        updated_shares=updated,
    )


def substitution_bias_pp(laspeyres_value: float, paasche_value: float) -> float:
    """Laspeyres minus Paasche, in percentage points of index level.

    The standard quantification of substitution bias: Laspeyres holds the
    old basket and so ignores substitution entirely, Paasche holds the new
    one and so credits all of it, and a superlative index sits between
    them. The gap is the range within which the choice of basket, rather
    than any price movement, determines the answer.
    """
    return (laspeyres_value - paasche_value) * 100.0


#: Every bilateral formula, by name, with the inputs it requires. Used by
#: the interface to offer only the formulae the loaded data can actually
#: support: a collection with no quantities cannot compute a Fisher, and
#: offering it anyway produces a NaN the user has to diagnose.
BILATERAL_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "laspeyres": ("q0",),
    "paasche": ("qt",),
    "fisher": ("q0", "qt"),
    "tornqvist": ("q0", "qt"),
    "walsh": ("q0", "qt"),
    "marshall_edgeworth": ("q0", "qt"),
    "geometric_laspeyres": ("q0",),
    "geometric_paasche": ("qt",),
    "lowe": ("qb",),
    "young": ("sb",),
}


def price_updating_from_panel(
    df: pd.DataFrame, *, weight_reference: pd.Timestamp, price_reference: pd.Timestamp,
    current: pd.Timestamp, price_col: str = "price_imputed", group: str = "category",
) -> pd.DataFrame:
    """Lowe against Young for each group of a price panel that carries a
    `weight` column, with the difference attributed to price updating.

    For each group: `p_b`, `p_0` and `p_t` are the items' prices at the
    weight reference, price reference and current periods; `s_b` is each
    item's weight at the weight reference period (its mean weight where
    the panel repeats it). Items priced in all three periods enter. One
    row per group with the Lowe and Young levels (base 100 at the price
    reference), the gap in points, and the aggregate price change between
    b and 0 that drives it; a group with too few items is reported with
    NaN rather than dropped.
    """
    rows = {}
    for key, d in df.groupby(group):
        pivot = d.pivot_table(index="period", columns="item_id", values=price_col, aggfunc="first")
        weights = d.dropna(subset=["weight"]).groupby("item_id")["weight"].mean()
        if not {weight_reference, price_reference, current} <= set(pivot.index):
            rows[str(key)] = {"lowe": np.nan, "young": np.nan, "difference_pp": np.nan,
                              "weight_price_change": np.nan, "n_items": 0}
            continue
        pb = pd.Series(pivot.loc[weight_reference].to_numpy(dtype=float), index=pivot.columns)
        p0 = pd.Series(pivot.loc[price_reference].to_numpy(dtype=float), index=pivot.columns)
        pt = pd.Series(pivot.loc[current].to_numpy(dtype=float), index=pivot.columns)
        shared = pivot.columns.intersection(weights.index)
        usable = [i for i in shared if np.isfinite(pb[i]) and np.isfinite(p0[i]) and np.isfinite(pt[i])
                  and pb[i] > 0 and p0[i] > 0 and pt[i] > 0 and weights[i] > 0]
        if len(usable) < 2:
            rows[str(key)] = {"lowe": np.nan, "young": np.nan, "difference_pp": np.nan,
                              "weight_price_change": np.nan, "n_items": len(usable)}
            continue
        report = price_updating_effect(p0.loc[usable], pt.loc[usable], pb.loc[usable],
                                       weights.loc[usable].astype(float))
        rows[str(key)] = {"lowe": report.lowe * 100.0, "young": report.young * 100.0,
                          "difference_pp": report.difference_pp,
                          "weight_price_change": report.weight_price_change,
                          "n_items": len(usable)}
    return pd.DataFrame(rows).T
