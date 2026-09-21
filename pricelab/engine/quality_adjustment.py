"""Quality adjustment: what to do when the item being priced is replaced
by a different one.

A matched-model index (everything `engine.index` computes) compares an
item only with itself. When an item leaves the sample and another
arrives, the two are never compared, so the price difference between them
-- which is partly quality and partly price -- is simply never measured.
That is a quality adjustment too, an implicit one, and usually a wrong
one: old models leave at run-out prices and new ones arrive at launch
prices, so the change the matched model skips is systematically not zero.

This module makes the comparison explicit. Each method here values the
quality difference between an old item and its replacement as a ratio
(`quality_ratio`: the new item's worth relative to the old, in price
terms), and everything else follows from the ratio: the new item's price
in old-quality terms, the part of the price gap attributed to quality
rather than price, and the effect on the elementary index of making the
adjustment rather than not. Every result carries a reason code naming the
method, because a reader of the published index needs to be able to ask
"how was that replacement valued?" and get an answer other than "somehow".

The methods, from the CPI Manual 2020, Chapter 6 ("Temporarily and
Permanently Missing Prices and Quality Change"):

implicit    overlap pricing; overall mean, targeted mean and class mean
            imputation. The quality difference is inferred from prices
            other than the two being compared. Link-to-show-no-change is
            here too, because it exists in practice, and it warns.
explicit    direct comparison (the replacement is judged comparable);
            quantity adjustment (package size); option cost (a feature
            that was an option is now standard, or the reverse). The
            quality difference is valued from information about the items.
hedonic     `engine.hedonic`, which values the difference from a
            regression of price on characteristics and hands back the
            same result object.

Multiplicative throughout: the adjustment is a ratio applied to a price,
not a fixed amount added to it, which the manual advises as the general
case because a ratio is invariant to the price level (the fixed-shipping-
cost exception it describes can be expressed through `option_cost`).

The ledger and the impact report
--------------------------------
An approved adjustment becomes a `QualityAdjustmentEntry` in
`RunConfig.quality_adjustment`, which is why the registry hash, the cache
key and every saved config already cover it. `apply_adjustments` links the
replacement onto the old item's series before imputation and indexing, so
the standard matched-model machinery then spans the replacement without
knowing it did. `impact_report` re-runs the pipeline three ways -- as
configured, with every replacement linked but valued at ratio 1, and with
no replacement linked at all -- and states, in index points and in
percentage points of annual inflation, how much of the headline is the
adjustments. The manual's own view is that this is the most scrutinised
number in a price index; it is computed here as a first-class result, not
recovered afterwards from a difference of two rounded tables.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import numpy as np
import pandas as pd

from ..core.config import QualityAdjustmentEntry, RunConfig
from .index import annualised_rate, year_on_year, years_span


class ReasonCode(StrEnum):
    """Why a replacement was valued the way it was. Recorded on every
    adjustment and in every ledger entry."""

    OVERLAP = "overlap"
    DIRECT_COMPARISON = "direct_comparison"
    QUANTITY_ADJUSTMENT = "quantity_adjustment"
    OPTION_COST = "option_cost"
    CLASS_MEAN = "class_mean_imputation"
    TARGETED_MEAN = "targeted_mean_imputation"
    OVERALL_MEAN = "overall_mean_imputation"
    LINK_NO_CHANGE = "link_to_show_no_change"
    HEDONIC = "hedonic"


class QualityAdjustmentError(ValueError):
    """The inputs cannot support the method asked for."""


class LinkToShowNoChangeWarning(UserWarning):
    """The replacement was linked in with its whole price difference
    attributed to quality. CPI Manual 2020, Chapter 6: "Linked to show no
    price change should not be used." """


@dataclass(frozen=True)
class CellContext:
    """The elementary cell the replacement sits in, for expressing an
    adjustment in index points. `n_items` is the matched items in the
    cell's link at the replacement period, the replacement included."""

    n_items: int
    formula: str = "jevons"
    base_level: float = 100.0
    cell_sum_prev: float | None = None
    """Sum of the cell's previous-period prices, needed for Dutot only."""


@dataclass(frozen=True)
class QualityAdjustment:
    """One valued replacement, and everything needed to read it."""

    old_item: str
    new_item: str
    category: str
    period: pd.Timestamp
    """First period the replacement's price stands in for the old item."""
    method: str
    """A `ReasonCode` value."""
    old_price: float
    """The old item's last price entering the comparison."""
    new_price: float
    """The replacement's price at `period`, as collected."""
    quality_ratio: float
    """New item's worth relative to old, in price terms. 1.0 is comparable."""
    adjusted_new_price: float
    """`new_price / quality_ratio`: the replacement priced in old-quality
    terms, which is the price the old item's series continues with."""
    adjustment_price: float
    """`new_price - adjusted_new_price`: the part of the collected price
    gap attributed to quality rather than to price change."""
    pure_price_relative: float
    """`adjusted_new_price / old_price`: the price change the index
    records across the replacement."""
    index_points: float
    """Effect on the cell's link, in points at `base_level`, of applying
    this adjustment rather than treating the raw replacement price as pure
    price change. NaN when no `CellContext` was supplied."""
    parameters: dict[str, Any] = field(default_factory=dict)
    justification: str = ""

    @property
    def reason_code(self) -> str:
        return self.method

    def to_entry(self, approved_by: str, approved_at: str | None = None) -> QualityAdjustmentEntry:
        """The ledger form of this adjustment, carrying who approved it."""
        return QualityAdjustmentEntry(
            old_item=self.old_item, new_item=self.new_item, category=self.category,
            period=str(self.period.date()), method=self.method,
            quality_ratio=float(self.quality_ratio),
            parameters={**self.parameters, "old_price": self.old_price, "new_price": self.new_price},
            justification=self.justification, approved_by=approved_by,
            approved_at=approved_at or datetime.now(UTC).isoformat())


def index_point_effect(
    quality_ratio: float, cell: CellContext | None, raw_relative: float, new_price: float
) -> float:
    """Index points, at the cell's base level, by which applying the
    adjustment moves the cell's period-on-period link compared with
    treating the raw replacement price as pure price change.

    Jevons: the link is a geometric mean of n relatives, so dividing one of
    them by `quality_ratio` multiplies the link by `quality_ratio^(-1/n)`.
    Carli: an arithmetic mean, so the link moves by `(raw/q - raw) / n`.
    Dutot: a ratio of sums, so by `new_price (1/q - 1) / sum_prev`.
    """
    if cell is None or cell.n_items <= 0:
        return float("nan")
    q, n, base = float(quality_ratio), cell.n_items, cell.base_level
    if cell.formula == "jevons":
        return float(base * (q ** (-1.0 / n) - 1.0))
    if cell.formula == "carli":
        return base * (raw_relative / q - raw_relative) / n
    if cell.formula == "dutot":
        if not cell.cell_sum_prev or cell.cell_sum_prev <= 0:
            raise QualityAdjustmentError(
                "Dutot index points need cell_sum_prev, the sum of the cell's previous-period "
                "prices, because a ratio of sums has no per-item closed form without it")
        return base * new_price * (1.0 / q - 1.0) / cell.cell_sum_prev
    raise QualityAdjustmentError(
        f"no index-point expression for formula {cell.formula!r}; use jevons, carli or dutot")


def _build(
    *, old_item: str, new_item: str, category: str, period: pd.Timestamp, method: ReasonCode,
    old_price: float, new_price: float, quality_ratio: float,
    parameters: dict[str, Any], cell: CellContext | None, justification: str,
) -> QualityAdjustment:
    for name, value in (("old_price", old_price), ("new_price", new_price),
                        ("quality_ratio", quality_ratio)):
        if not np.isfinite(value) or value <= 0:
            raise QualityAdjustmentError(f"{name} must be a finite positive number, got {value!r}")
    adjusted = new_price / quality_ratio
    raw_relative = new_price / old_price
    return QualityAdjustment(
        old_item=old_item, new_item=new_item, category=category, period=pd.Timestamp(period),
        method=str(method), old_price=float(old_price), new_price=float(new_price),
        quality_ratio=float(quality_ratio), adjusted_new_price=float(adjusted),
        adjustment_price=float(new_price - adjusted),
        pure_price_relative=float(adjusted / old_price),
        index_points=index_point_effect(quality_ratio, cell, raw_relative, new_price),
        parameters=parameters, justification=justification)


# ---------------------------------------------------------------------
# Implicit methods
# ---------------------------------------------------------------------
def overlap(
    old_item: str, new_item: str, category: str,
    old_prices: pd.Series, new_prices: pd.Series, overlap_period: pd.Timestamp,
    *, period: pd.Timestamp | None = None, cell: CellContext | None = None,
    justification: str = "",
) -> QualityAdjustment:
    """Overlap pricing: both items are priced in one period, and the ratio
    of their prices there is taken as the ratio of their qualities.

        q = p_new(overlap) / p_old(overlap)

    CPI Manual 2020, Chapter 6, "Overlap method" and equations (6.5)-(6.8).

    The assumption is that in the overlap period the market priced the two
    items' quality difference and nothing else: no launch premium on the
    new, no clearance discount on the old. The manual's caution is that
    both are common precisely at the moment a replacement happens, which is
    why it advises replacing models before the old one reaches the end of
    its life. The result is that the index records the *new* item's own
    price change from the overlap period onward, and none of the level
    gap between the two.

    `period` is the first period the new item stands in for the old; it
    defaults to the first period in `new_prices` after the overlap.
    """
    if overlap_period not in old_prices.index or overlap_period not in new_prices.index:
        raise QualityAdjustmentError(
            f"overlap pricing needs both items priced at {pd.Timestamp(overlap_period):%Y-%m-%d}; "
            "an overlap price that one of them does not have is an imputation, and should be "
            "made as one (targeted_mean or class_mean) so the assumption is on the record")
    p_old, p_new = float(old_prices.loc[overlap_period]), float(new_prices.loc[overlap_period])
    if period is None:
        later = new_prices.index[new_prices.index > overlap_period]
        if len(later) == 0:
            raise QualityAdjustmentError(
                "the new item has no price after the overlap period, so there is nothing to link")
        period = later[0]
    if period not in new_prices.index:
        raise QualityAdjustmentError(f"the new item has no price at {pd.Timestamp(period):%Y-%m-%d}")
    return _build(
        old_item=old_item, new_item=new_item, category=category, period=period,
        method=ReasonCode.OVERLAP, old_price=p_old, new_price=float(new_prices.loc[period]),
        quality_ratio=p_new / p_old,
        parameters={"overlap_period": str(pd.Timestamp(overlap_period).date()),
                    "old_price_at_overlap": p_old, "new_price_at_overlap": p_new},
        cell=cell, justification=justification)


def link_to_show_no_change(
    old_item: str, new_item: str, category: str, period: pd.Timestamp,
    old_price: float, new_price: float, *, cell: CellContext | None = None,
    justification: str = "",
) -> QualityAdjustment:
    """The whole gap between the old item's last price and the replacement's
    first price is attributed to quality; the index records no change
    across the replacement.

    CPI Manual 2020, Chapter 6, Table 6.4a: an old item at 25 then 28, a
    replacement at 35, linked to show no change, gives a January-to-March
    change of 28/25 x 35/35 = 1.12 -- every bit of the 28-to-35 step is
    called quality. The manual's verdict: "Linked to show no price change
    should not be used." It is the overlap method with the overlap price
    imputed to equal the replacement's own price, and it biases the index
    downward whenever quality-adjusted prices are rising.

    Provided because compilation systems do this by default, and a method
    that exists in the world is better represented here with a warning
    than left as something the tool cannot even name.
    """
    warnings.warn(
        f"{old_item} -> {new_item} linked to show no price change: the entire "
        f"{old_price:.4g} -> {new_price:.4g} gap is being attributed to quality. CPI Manual "
        "2020 Chapter 6 says this method should not be used; prefer an overlap price, a mean "
        "imputation or an explicit adjustment.", LinkToShowNoChangeWarning, stacklevel=2)
    return _build(
        old_item=old_item, new_item=new_item, category=category, period=period,
        method=ReasonCode.LINK_NO_CHANGE, old_price=old_price, new_price=new_price,
        quality_ratio=new_price / old_price, parameters={}, cell=cell,
        justification=justification)


def _geometric_mean_relative(prev: pd.Series, curr: pd.Series, what: str) -> tuple[float, int]:
    common = prev.index.intersection(curr.index)
    a = prev.reindex(common).to_numpy(dtype=float)
    b = curr.reindex(common).to_numpy(dtype=float)
    ok = np.isfinite(a) & np.isfinite(b) & (a > 0) & (b > 0)
    if not ok.any():
        raise QualityAdjustmentError(
            f"{what} needs at least one peer item priced in both periods; none was")
    return float(np.exp(np.mean(np.log(b[ok] / a[ok])))), int(ok.sum())


def _mean_imputation(
    method: ReasonCode, old_item: str, new_item: str, category: str, period: pd.Timestamp,
    old_price: float, new_price: float, peer_prev: pd.Series, peer_curr: pd.Series,
    cell: CellContext | None, justification: str,
) -> QualityAdjustment:
    relative, n_peers = _geometric_mean_relative(peer_prev, peer_curr, str(method))
    return _build(
        old_item=old_item, new_item=new_item, category=category, period=period, method=method,
        old_price=old_price, new_price=new_price,
        quality_ratio=(new_price / old_price) / relative,
        parameters={"imputed_price_relative": relative, "n_peers": n_peers,
                    "peers": [str(i) for i in peer_prev.index.intersection(peer_curr.index)]},
        cell=cell, justification=justification)


def targeted_mean(
    old_item: str, new_item: str, category: str, period: pd.Timestamp,
    old_price: float, new_price: float, peer_prev: pd.Series, peer_curr: pd.Series,
    *, cell: CellContext | None = None, justification: str = "",
) -> QualityAdjustment:
    """The old item's price is assumed to have moved like a targeted set of
    similar items -- same outlet type, same region, same market segment --
    and whatever of the replacement's price is left over is quality.

        pure relative = geometric mean over peers of p_curr / p_prev
        q = (p_new / p_old) / pure relative

    CPI Manual 2020, Chapter 6, "Targeted mean imputation": outlet F's
    price of 5.99 carried by outlets D and E's matched change,
    (5.65 x 6.90 / (5.49 x 6.50))^(1/2) = 1.04522, gives 6.26.

    `peer_prev` and `peer_curr` are the peers' prices, indexed by item, in
    the period before the replacement and at it; only items priced in both
    contribute. Targeting is a judgement about which items move alike,
    and the peers are recorded so that judgement is on the record.
    """
    return _mean_imputation(ReasonCode.TARGETED_MEAN, old_item, new_item, category, period,
                            old_price, new_price, peer_prev, peer_curr, cell, justification)


def overall_mean(
    old_item: str, new_item: str, category: str, period: pd.Timestamp,
    old_price: float, new_price: float, peer_prev: pd.Series, peer_curr: pd.Series,
    *, cell: CellContext | None = None, justification: str = "",
) -> QualityAdjustment:
    """As `targeted_mean`, with every matched item in the elementary
    aggregate as the peer set rather than a chosen subset.

    CPI Manual 2020, Chapter 6, "Overall mean imputation" and equation
    (6.4): the Jevons relative over outlets A to E, 5.67 / 5.54 =
    1.023765, carries outlet F's 5.99 to 6.13. The manual notes this is
    numerically the same as dropping the item from the link -- which is
    exactly what a matched-model index does by default -- so the method's
    contribution is to make that default an explicit, recorded choice with
    the replacement then linked in behind it.
    """
    return _mean_imputation(ReasonCode.OVERALL_MEAN, old_item, new_item, category, period,
                            old_price, new_price, peer_prev, peer_curr, cell, justification)


def class_mean(
    old_item: str, new_item: str, category: str, period: pd.Timestamp,
    old_price: float, new_price: float, peer_relatives: Sequence[float] | pd.Series,
    *, cell: CellContext | None = None, justification: str = "",
) -> QualityAdjustment:
    """The old item's price is assumed to have moved like other items in the
    class that were *themselves replaced* in the same link and valued
    explicitly (or judged comparable), rather than like the continuing
    items.

        pure relative = geometric mean of peers' quality-adjusted relatives
        q = (p_new / p_old) / pure relative

    CPI Manual 2020, Chapter 6, "Class mean imputation". The argument for
    it over targeted mean: continuing items are "a flawed proxy for the
    pure price component" of a replacement, because prices behave
    differently at the start and end of a model's life than in the middle.
    The peers here are therefore relatives that have already been through
    a quality adjustment, which is also why this method cannot be the
    first one used in a class: it needs the others to exist.
    """
    rel = np.asarray(list(peer_relatives), dtype=float)
    rel = rel[np.isfinite(rel) & (rel > 0)]
    if rel.size == 0:
        raise QualityAdjustmentError(
            "class_mean needs at least one quality-adjusted relative from another replacement "
            "in the same class; with none, use targeted_mean or overall_mean, whose peers are "
            "continuing items")
    relative = float(np.exp(np.mean(np.log(rel))))
    return _build(
        old_item=old_item, new_item=new_item, category=category, period=period,
        method=ReasonCode.CLASS_MEAN, old_price=old_price, new_price=new_price,
        quality_ratio=(new_price / old_price) / relative,
        parameters={"imputed_price_relative": relative, "n_peers": int(rel.size),
                    "peer_relatives": [float(r) for r in rel]},
        cell=cell, justification=justification)


# ---------------------------------------------------------------------
# Explicit methods
# ---------------------------------------------------------------------
def direct_comparison(
    old_item: str, new_item: str, category: str, period: pd.Timestamp,
    old_price: float, new_price: float, *, cell: CellContext | None = None,
    justification: str = "",
) -> QualityAdjustment:
    """The replacement is judged comparable: same quality, so the whole
    price gap is price change.

        q = 1

    CPI Manual 2020, Chapter 6, "Direct comparison" (collector code C,
    comparable replacement). The judgement is the method, and it is a
    judgement about the items -- "so similar it can be assumed to have had
    more or less the same quality characteristics" -- which is why a
    justification is worth writing even though the arithmetic is trivial.
    """
    return _build(
        old_item=old_item, new_item=new_item, category=category, period=period,
        method=ReasonCode.DIRECT_COMPARISON, old_price=old_price, new_price=new_price,
        quality_ratio=1.0, parameters={}, cell=cell, justification=justification)


def quantity_adjustment(
    old_item: str, new_item: str, category: str, period: pd.Timestamp,
    old_price: float, new_price: float, old_quantity: float, new_quantity: float,
    *, unit: str = "", cell: CellContext | None = None, justification: str = "",
) -> QualityAdjustment:
    """The items differ in size and are otherwise the same, so they are
    compared per unit.

        q = quantity_new / quantity_old

    CPI Manual 2020, Chapter 6, "Quantity adjustment" and Table 6.5: a
    0.25 kg bag of flour at 0.75 replaced by a 0.5 kg bag at 1.50 is no
    price change (3.00 per kg both times); the same 0.25 kg bag at 1.25
    is a rise to 5.00 per kg.

    The manual's caveat is that price is rarely proportional to size
    (larger packs are usually cheaper per unit), so this is only right
    when the size change is modest or the product is genuinely priced per
    unit; a large size change is better treated as a non-comparable
    replacement.
    """
    if not (np.isfinite(old_quantity) and np.isfinite(new_quantity)
            and old_quantity > 0 and new_quantity > 0):
        raise QualityAdjustmentError("quantities must be finite and positive")
    return _build(
        old_item=old_item, new_item=new_item, category=category, period=period,
        method=ReasonCode.QUANTITY_ADJUSTMENT, old_price=old_price, new_price=new_price,
        quality_ratio=new_quantity / old_quantity,
        parameters={"old_quantity": float(old_quantity), "new_quantity": float(new_quantity),
                    "unit": unit, "old_unit_price": old_price / old_quantity,
                    "new_unit_price": new_price / new_quantity},
        cell=cell, justification=justification)


def option_cost(
    old_item: str, new_item: str, category: str, period: pd.Timestamp,
    old_price: float, new_price: float, option_value: float,
    *, feature_added: bool = True, share_valued: float = 1.0,
    cell: CellContext | None = None, justification: str = "",
) -> QualityAdjustment:
    """A feature that was an option at a known price is now standard (or a
    standard feature has been dropped), and the old price is adjusted by
    what the feature cost.

        adjusted old price = p_old + option_value x share_valued   (added)
                           = p_old - option_value x share_valued   (removed)
        q = adjusted old price / p_old

    CPI Manual 2020, Chapter 6, "Differences in feature/option costs": an
    item at 10,000 then 10,500, the new one including as standard a
    feature that was a 300 option, is a rise of 10,500 / 10,300 = 1.01942.

    `share_valued` is the manual's own qualification: a feature made
    standard is usually worth less to the average buyer than its price as
    an option (it costs less to fit as standard, and some buyers would not
    have chosen it), so valuing it at a fraction of the option price is
    often the better estimate. The fraction is a judgement, recorded here.
    """
    if not np.isfinite(option_value) or option_value < 0:
        raise QualityAdjustmentError("option_value must be finite and non-negative")
    if not 0.0 <= share_valued <= 1.0:
        raise QualityAdjustmentError("share_valued is a fraction between 0 and 1")
    delta = option_value * share_valued * (1.0 if feature_added else -1.0)
    adjusted_old = old_price + delta
    if adjusted_old <= 0:
        raise QualityAdjustmentError(
            "removing that option would leave the old item with a non-positive price")
    return _build(
        old_item=old_item, new_item=new_item, category=category, period=period,
        method=ReasonCode.OPTION_COST, old_price=old_price, new_price=new_price,
        quality_ratio=adjusted_old / old_price,
        parameters={"option_value": float(option_value), "feature_added": feature_added,
                    "share_valued": float(share_valued), "adjusted_old_price": adjusted_old},
        cell=cell, justification=justification)


# ---------------------------------------------------------------------
# Applying a ledger to a panel
# ---------------------------------------------------------------------
REPLACEMENT_FLAG = "replacement_flag"
QUALITY_ADJUSTMENT_FLAG = "quality_adjustment_flag"
REPLACED_ITEM_ID = "replaced_item_id"


def apply_adjustments(
    df: pd.DataFrame,
    entries: Iterable[QualityAdjustmentEntry],
    price_col: str = "price_clean",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Link each approved replacement onto the old item's series.

    From `entry.period` onward the new item's rows carry the old item's
    `item_id`, with `price_col` divided by `quality_ratio` so the series
    continues in old-quality terms; the old item's own rows at or after
    that period, and the new item's before it, are dropped -- the first
    are what was replaced, the second are the overlap or entry rows that
    informed the valuation and would otherwise linger as a one-period
    orphan item. Nothing is dropped silently: the second frame returned
    lists every row moved or removed, and the linked rows are flagged
    (`quality_adjustment_flag` = method, `replacement_flag` at the link
    period, `replaced_item_id` = the new item's original identifier).

    Dividing the replacement's prices is the "current period adjustment"
    variant. The manual prefers adjusting the reference-period price
    once; in a chained short-term index the two are the same relatives,
    and adjusting the incoming series is what keeps `price_reported` --
    the collected price -- untouched.
    """
    out = df.copy()
    for col, default in ((REPLACEMENT_FLAG, False), (QUALITY_ADJUSTMENT_FLAG, ""),
                         (REPLACED_ITEM_ID, "")):
        if col not in out.columns:
            out[col] = default
    log_rows: list[dict[str, Any]] = []

    for entry in entries:
        period = pd.Timestamp(entry.period)
        in_cat = out["category"] == entry.category
        old_rows = in_cat & (out["item_id"] == entry.old_item)
        new_rows = in_cat & (out["item_id"] == entry.new_item)
        if not old_rows.any() or not new_rows.any():
            raise QualityAdjustmentError(
                f"ledger entry {entry.old_item!r} -> {entry.new_item!r} in {entry.category!r}: "
                "one or both items are not in the data for that category")
        if not (new_rows & (out["period"] >= period)).any():
            raise QualityAdjustmentError(
                f"ledger entry {entry.old_item!r} -> {entry.new_item!r}: the new item has no "
                f"rows at or after {period:%Y-%m-%d}")

        drop_old = old_rows & (out["period"] >= period)
        drop_new = new_rows & (out["period"] < period)
        link = new_rows & (out["period"] >= period)
        for mask, what in ((drop_old, "old item row replaced"), (drop_new, "new item row before link")):
            for _, r in out.loc[mask].iterrows():
                log_rows.append({"old_item": entry.old_item, "new_item": entry.new_item,
                                 "period": r["period"], "action": what, "item_id": r["item_id"]})
        n_link = int(link.sum())
        out.loc[link, price_col] = out.loc[link, price_col] / entry.quality_ratio
        out.loc[link, REPLACED_ITEM_ID] = entry.new_item
        out.loc[link, QUALITY_ADJUSTMENT_FLAG] = entry.method
        out.loc[link, "item_id"] = entry.old_item
        out.loc[link & (out["period"] == period), REPLACEMENT_FLAG] = True
        log_rows.append({"old_item": entry.old_item, "new_item": entry.new_item,
                         "period": period, "action": f"{n_link} new item rows linked",
                         "item_id": entry.old_item})
        out = out.loc[~(drop_old | drop_new)]

    log = pd.DataFrame(log_rows, columns=["old_item", "new_item", "period", "action", "item_id"])
    return out.reset_index(drop=True), log


# ---------------------------------------------------------------------
# The impact report
# ---------------------------------------------------------------------
@dataclass
class ImpactReport:
    """How much of the headline is the quality adjustments."""

    headline: str
    """Column of the index frame the report is about ("All items" when
    the collection has more than one category)."""
    final_period: pd.Timestamp
    scenarios: pd.DataFrame
    """One row per scenario -- `as_configured`, `linked_unadjusted`,
    `no_link` -- with the headline's final level and its annual rate."""
    adjustment_effect_points: float
    """as_configured minus linked_unadjusted, in index points at the final
    period: the effect of the *sizes* of the adjustments."""
    adjustment_effect_annual_pp: float
    """The same, in percentage points of the annual rate."""
    linking_effect_points: float
    """as_configured minus no_link: the effect of linking the replacements
    at all, sizes included, against the matched-model default that would
    have skipped them."""
    linking_effect_annual_pp: float
    per_entry: pd.DataFrame
    """Each entry's own contribution to `adjustment_effect_points`,
    measured by setting that entry's ratio to 1 and leaving the rest, plus
    the interaction residual that makes the column sum exactly."""
    interaction_residual_points: float
    per_category: pd.DataFrame
    """The three scenarios' final levels per category column."""
    annual_measure: str
    """"year_on_year" when the series spans 12 or more periods, else
    "annualised_over_span"; stated because the two are not the same
    number and a report should say which it used."""

    @property
    def n_entries(self) -> int:
        return int(len(self.per_entry.index.difference(["interaction residual"])))


def _with_ratios(cfg: RunConfig, ratio_for: Callable[[QualityAdjustmentEntry], float | None]) -> RunConfig:
    """A copy of `cfg` whose ledger entries have their ratios replaced, or
    are dropped when the callback returns None."""
    entries = []
    for e in cfg.quality_adjustment.entries:
        r = ratio_for(e)
        if r is not None:
            entries.append(e.model_copy(update={"quality_ratio": r}))
    return cfg.model_copy(update={"quality_adjustment": cfg.quality_adjustment.model_copy(
        update={"entries": entries})}, deep=True)


def _annual_rate(indices: pd.DataFrame, column: str) -> tuple[float, str]:
    series = indices[column]
    if len(series) > 12:
        yoy = year_on_year(indices)[column].iloc[-1]
        if np.isfinite(yoy):
            return float(yoy), "year_on_year"
    years = years_span(pd.DatetimeIndex(series.index))
    if years <= 0 or not np.isfinite(series.iloc[-1]) or not np.isfinite(series.iloc[0]):
        return float("nan"), "annualised_over_span"
    return float(annualised_rate(series.iloc[-1] / series.iloc[0], years)), "annualised_over_span"


def impact_report(
    df: pd.DataFrame, cfg: RunConfig,
    run: Callable[[pd.DataFrame, RunConfig], dict[str, Any]] | None = None,
) -> ImpactReport:
    """Quantify the quality adjustments' share of the headline.

    Three runs of the same pipeline on the same data: as configured; with
    every replacement still linked but valued at ratio 1 (the collected
    price gap treated as pure price change); and with no replacement
    linked (the matched-model default, which compares nothing across a
    replacement). The differences are the adjustments' effect and the
    linking's effect, in index points at the final period and in
    percentage points of annual inflation.

    Per-entry attribution is by leave-one-out on the ratio: each entry's
    effect is what the headline loses when that entry alone is set to
    ratio 1. Those effects do not sum exactly to the total when entries
    interact (two replacements in one cell), so the residual is reported
    as its own row rather than spread over the entries, and the column
    then sums to the total exactly.
    """
    if run is None:
        from .. import run_pipeline
        run = run_pipeline

    base = run(df, cfg)
    if "indices" not in base:
        raise QualityAdjustmentError("the run did not produce an index; see its validation report")
    indices = base["indices"]
    headline = "All items" if "All items" in indices.columns else str(indices.columns[0])
    final_period = pd.Timestamp(indices.index[-1])

    linked = run(df, _with_ratios(cfg, lambda _e: 1.0))["indices"]
    unlinked = run(df, _with_ratios(cfg, lambda _e: None))["indices"]

    def final(frame: pd.DataFrame, column: str) -> float:
        return float(frame[column].iloc[-1]) if column in frame.columns else float("nan")

    levels: dict[str, float] = {}
    annuals: dict[str, float] = {}
    measure = "year_on_year"
    for name, frame in (("as_configured", indices), ("linked_unadjusted", linked),
                        ("no_link", unlinked)):
        annuals[name], measure = _annual_rate(frame, headline)
        levels[name] = final(frame, headline)
    scenarios = pd.DataFrame({"final_level": levels, "annual_rate_pct": annuals})

    adj_points = levels["as_configured"] - levels["linked_unadjusted"]
    adj_pp = annuals["as_configured"] - annuals["linked_unadjusted"]
    link_points = levels["as_configured"] - levels["no_link"]
    link_pp = annuals["as_configured"] - annuals["no_link"]

    def _ratio_without(target: QualityAdjustmentEntry) -> Callable[[QualityAdjustmentEntry], float | None]:
        def ratio(x: QualityAdjustmentEntry) -> float | None:
            same = (x.old_item, x.new_item) == (target.old_item, target.new_item)
            return 1.0 if same else x.quality_ratio
        return ratio

    per_entry_rows: dict[str, dict[str, float | str]] = {}
    for e in cfg.quality_adjustment.entries:
        without = run(df, _with_ratios(cfg, _ratio_without(e)))["indices"]
        key = f"{e.old_item} -> {e.new_item}"
        per_entry_rows[key] = {
            "category": e.category, "method": e.method, "quality_ratio": e.quality_ratio,
            "effect_points": float(final(indices, headline) - final(without, headline))}
    per_entry = pd.DataFrame(per_entry_rows).T if per_entry_rows else pd.DataFrame(
        columns=["category", "method", "quality_ratio", "effect_points"])
    explained = float(per_entry["effect_points"].astype(float).sum()) if len(per_entry) else 0.0
    residual = float(adj_points) - explained
    per_entry.loc["interaction residual"] = {"category": "", "method": "", "quality_ratio": np.nan,
                                             "effect_points": residual}
    per_entry["effect_points"] = per_entry["effect_points"].astype(float)

    per_category = pd.DataFrame({
        "as_configured": indices.iloc[-1],
        "linked_unadjusted": linked.iloc[-1].reindex(indices.columns),
        "no_link": unlinked.iloc[-1].reindex(indices.columns)})
    per_category["adjustment_effect_points"] = (
        per_category["as_configured"] - per_category["linked_unadjusted"])
    per_category["linking_effect_points"] = per_category["as_configured"] - per_category["no_link"]

    return ImpactReport(
        headline=headline, final_period=final_period, scenarios=scenarios,
        adjustment_effect_points=float(adj_points), adjustment_effect_annual_pp=float(adj_pp),
        linking_effect_points=float(link_points), linking_effect_annual_pp=float(link_pp),
        per_entry=per_entry, interaction_residual_points=residual,
        per_category=per_category, annual_measure=measure)


def ledger_frame(entries: Iterable[QualityAdjustmentEntry]) -> pd.DataFrame:
    """The ledger as a table: one row per approved replacement."""
    rows = [{
        "period": e.period, "category": e.category, "old_item": e.old_item,
        "new_item": e.new_item, "method": e.method, "quality_ratio": e.quality_ratio,
        "old_price": e.parameters.get("old_price"), "new_price": e.parameters.get("new_price"),
        "justification": e.justification, "approved_by": e.approved_by,
        "approved_at": e.approved_at} for e in entries]
    return pd.DataFrame(rows, columns=[
        "period", "category", "old_item", "new_item", "method", "quality_ratio", "old_price",
        "new_price", "justification", "approved_by", "approved_at"])
