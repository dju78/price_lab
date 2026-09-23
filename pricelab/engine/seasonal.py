"""Strictly seasonal items, the Rothwell index, counter-seasonal estimation,
and seasonal adjustment.

Two different problems share the word "seasonal" and this module keeps them
apart, because conflating them is how a price index acquires a movement
nobody put there.

The first is **strictly seasonal items**: products that are not on the shelf
for part of every year. Strawberries in February have no price, and the
question is not how to smooth them but what the basket is supposed to mean
while they are gone. A matched index simply drops them, which quietly
redistributes their weight and scores their return in June as price change.
The manual offers two families of answer and this module implements both,
because they give different numbers on the same data:

class confinement  the absent item's weight stays inside its class and is
                   carried by whichever of the class's items are in season.
                   The class keeps its full share of the basket all year, so
                   the basket's shape never changes; what changes is which
                   items inside the class are representing it.
weight update      the absent item's weight leaves the basket for the months
                   it is absent, and every remaining weight is renormalised.
                   The basket's shape now varies month by month with what is
                   actually on sale, which is more honest about what was
                   priced and less comparable between months.

Neither is right. Confinement assumes the in-season items of a class move the
way the absent one would have; weight update assumes the absent item's
expenditure simply did not happen, rather than moving to a substitute. The
module computes both and reports the gap, which on a collection with a real
seasonal item is not small.

The second problem is **seasonal variation in prices that are collected all
year**: the index genuinely rises every December and falls every January, and
a reader wants to know what happened *apart* from that. This is seasonal
adjustment, and it is the single most dangerous operation in this platform,
because an adjusted series is smooth and smooth looks authoritative. Two
rules are enforced rather than recommended:

1. Whichever engine actually ran is named in the result and travels with it
   into every output. X-13ARIMA-SEATS is what statistical offices use and
   what a reader assumes when they see "seasonally adjusted". STL is a
   robust loess decomposition -- a perfectly good one, and not the same
   thing: it has no trading-day or Easter regressors, no outlier model, no
   ARIMA extension of the series ends, and no quality diagnostics of the
   kind X-13 publishes. Presenting an STL result as "the seasonal
   adjustment" without saying so is a misrepresentation, so
   `SeasonalAdjustment.label` exists and every caller prints it.
2. The unadjusted series is carried alongside the adjusted one everywhere.
   `SeasonalAdjustment` holds both, so there is no code path on which an
   output can have the adjusted series without also having what it was
   adjusted from.

Sources: CPI Manual 2020, Chapter 11 (seasonal products; strictly seasonal
items and the treatment options, paras 11.10-11.18; the year-over-year and
rolling year indices, which live in `engine.multilateral`) and Chapter 14
(seasonal adjustment). Rothwell (1958), "Use of varying seasonal weights in
price index construction", JASA 53, and the ONS *CPI Technical Manual* for
the form used here. X-13ARIMA-SEATS: US Census Bureau. STL: Cleveland,
Cleveland, McRae and Terpenning (1990).
"""

from __future__ import annotations

import os
import shutil
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

import numpy as np
import pandas as pd

from ..core.config import IndexConfig, SeasonalConfig

#: The strictly seasonal item treatments, in the order the comparison lists.
TREATMENTS: tuple[str, ...] = ("class_confinement", "weight_update")

TREATMENT_LABELS: dict[str, str] = {
    "class_confinement": "Class confinement",
    "weight_update": "Weight update",
}

#: Seasonal adjustment engines, best first.
ENGINES: tuple[str, ...] = ("x13", "stl")

ENGINE_LABELS: dict[str, str] = {
    "x13": "X-13ARIMA-SEATS",
    "stl": "STL (seasonal-trend decomposition by loess)",
}


class SeasonalError(ValueError):
    """Raised when the data cannot support the seasonal operation asked for."""


# ---------------------------------------------------------------------
# Strictly seasonal items
# ---------------------------------------------------------------------
def season_of(periods: pd.Series, periods_per_year: int = 12) -> pd.Series:
    """The calendar position of each period within its year.

    Month for monthly data, quarter for quarterly, day-of-year otherwise.
    One function so every part of this module slices a year the same way;
    two slightly different notions of "the same calendar period" inside one
    module is how a seasonal factor ends up applied to the wrong month.
    """
    stamps = pd.to_datetime(periods)
    if periods_per_year == 12:
        return stamps.dt.month
    if periods_per_year == 4:
        return stamps.dt.quarter
    return stamps.dt.dayofyear


def strictly_seasonal_items(df: pd.DataFrame, cfg: SeasonalConfig | None = None, *,
                            price_col: str = "price_imputed") -> pd.DataFrame:
    """Which items are off the shelf for part of every year, and when.

    An item qualifies when it has been observed across at least
    `min_years` years, is priced in some calendar periods and never in
    others, and is in season in no more than `max_in_season_share` of them.
    The repetition is what distinguishes a season from a gap: an item
    missing for three months once is a collection failure and belongs to
    `engine.imputation`; an item missing for the same three months every
    year is a season and belongs here. Applying a seasonal rule to a
    collection failure invents a pattern and then removes it.

    Returns one row per item with the calendar periods it is in and out of
    season, how many years it was observed over, and whether it qualifies --
    including the items that did not, with the count that disqualified them,
    because "no strictly seasonal items were found" is a finding a reader
    should be able to check rather than take on trust.
    """
    cfg = cfg or SeasonalConfig()
    required = {"period", "item_id"}
    if not required <= set(df.columns):
        raise SeasonalError(
            f"a seasonal analysis needs {sorted(required)} columns; this frame has "
            f"{list(df.columns)[:8]}")
    price = price_col if price_col in df.columns else "price_reported"
    if price not in df.columns:
        raise SeasonalError(f"neither {price_col!r} nor 'price_reported' is a column here")

    frame = df.loc[:, ["period", "item_id", price]].copy()
    frame["period"] = pd.to_datetime(frame["period"])
    frame["season"] = season_of(frame["period"], cfg.periods_per_year)
    frame["year"] = frame["period"].dt.year
    frame["priced"] = pd.to_numeric(frame[price], errors="coerce").gt(0).fillna(False)

    all_seasons = sorted(frame["season"].unique())
    rows: list[dict[str, Any]] = []
    for item, part in frame.groupby("item_id"):
        priced = part[part["priced"]]
        years = int(priced["year"].nunique())
        in_season = sorted(priced["season"].unique())
        out_of_season = [s for s in all_seasons if s not in in_season]
        share = len(in_season) / len(all_seasons) if all_seasons else float("nan")
        qualifies = (years >= cfg.min_years and bool(out_of_season)
                     and share <= cfg.max_in_season_share)
        reason = ""
        if not qualifies:
            if years < cfg.min_years:
                reason = (f"observed over {years} year(s), fewer than the {cfg.min_years} "
                          "needed before an absence repeats")
            elif not out_of_season:
                reason = "priced in every calendar period, so it has no off season"
            else:
                reason = (f"in season in {share:.0%} of calendar periods, above the "
                          f"{cfg.max_in_season_share:.0%} ceiling: gaps, not a season")
        rows.append({
            "item_id": str(item), "years_observed": years,
            "in_season": tuple(int(s) for s in in_season),
            "out_of_season": tuple(int(s) for s in out_of_season),
            "in_season_share": share, "strictly_seasonal": qualifies,
            "excluded_because": reason})
    return pd.DataFrame(rows).set_index("item_id").sort_values(
        ["strictly_seasonal", "in_season_share"], ascending=[False, True])


@dataclass(frozen=True)
class SeasonalTreatmentResult:
    """One strictly seasonal item treatment, compiled."""

    treatment: str
    indices: pd.DataFrame
    """Index per category, plus "All items" under this treatment's own
    aggregation rule."""
    weights_by_period: pd.DataFrame
    """The weight each category carried in each period. Constant under class
    confinement -- that is what confinement means -- and varying under weight
    update, which is what makes the two different."""
    seasonal_items: tuple[str, ...]
    notes: tuple[str, ...] = ()

    @property
    def headline(self) -> pd.Series:
        return (self.indices["All items"] if "All items" in self.indices.columns
                else self.indices.iloc[:, 0])

    @property
    def label(self) -> str:
        return TREATMENT_LABELS.get(self.treatment, self.treatment)


def _category_indices(df: pd.DataFrame, index_cfg: IndexConfig,
                      price_col: str) -> pd.DataFrame:
    from .index import build_index

    series: dict[str, pd.Series] = {}
    for key, part in df.groupby("category"):
        series[str(key)] = build_index(part, index_cfg, price_col)["index"]
    return pd.DataFrame(series)


def _in_season_weights(df: pd.DataFrame, seasonal: pd.DataFrame, cfg: SeasonalConfig,
                       periods: pd.DatetimeIndex, categories: Sequence[str]) -> pd.DataFrame:
    """Per period, each category's weight with its out-of-season items removed.

    Built by subtraction, deliberately: start from exactly the constant
    weights class confinement uses, and take away only the weight of items
    whose own calendar period is outside their own observed season. Nothing
    else is allowed to move it.

    That construction is what makes the comparison between the two
    treatments mean something. Computing this weight instead as "the weight
    of the items that have a price this month" would also subtract items an
    enumerator happened to miss, and items that had not been introduced yet
    or had already been replaced -- so on a collection with ordinary churn
    and ordinary non-response the two treatments would differ substantially
    with no seasonal item anywhere in the data, and the gap would be
    reported as a seasonal judgement when it was nothing of the kind. Here,
    a collection with no strictly seasonal item gives the two treatments
    identical weights and therefore an identical series, which is the right
    answer and is asserted as a test.

    An item with no usable weight contributes nothing, exactly as it does to
    every other weighted figure in this engine, and a collection with no
    weight column gives one unit per item.
    """
    frame = df.loc[:, ["item_id", "category"]].copy()
    frame["item_id"] = frame["item_id"].astype(str)
    frame["category"] = frame["category"].astype(str)
    if "weight" in df.columns and df["weight"].notna().any():
        frame["weight"] = pd.to_numeric(df["weight"], errors="coerce").to_numpy()
    else:
        frame["weight"] = 1.0
    # Per-item weight as the mean over its rows, then summed per category:
    # the same construction `engine.index.category_weights` uses, so the two
    # treatments genuinely start from one set of numbers.
    per_item = frame.dropna(subset=["weight"]).groupby(["category", "item_id"])["weight"].mean()
    constant = per_item.groupby(level="category").sum()

    columns = [c for c in categories if c != "All items"]
    weights = pd.DataFrame(
        {c: pd.Series(float(constant.get(c, 0.0)), index=periods, dtype=float)
         for c in columns})
    if not len(seasonal):
        return weights

    season = season_of(pd.Series(periods), cfg.periods_per_year).to_numpy()
    category_of = per_item.index.to_frame(index=False).set_index("item_id")["category"]
    for item, out_of_season in seasonal.loc[
            seasonal["strictly_seasonal"], "out_of_season"].items():
        name = str(item)
        category = str(category_of.get(name, ""))
        if category not in weights.columns:
            continue
        weight = float(per_item.get((category, name), 0.0))
        if weight <= 0:
            continue
        off = np.isin(season, np.asarray(tuple(out_of_season), dtype=int))
        weights.loc[off, category] = weights.loc[off, category] - weight
    return weights.clip(lower=0.0)


def _chained_weighted_aggregate(indices: pd.DataFrame, weights_by_period: pd.DataFrame,
                                base_value: float = 100.0) -> pd.Series:
    """Chain a weighted mean of the categories' period-on-period relatives.

        I(t) = I(t-1) x [ sum_c w_c(t) I_c(t)/I_c(t-1) ] / sum_c w_c(t)

    Both strictly seasonal treatments aggregate through this one function,
    and differ only in the weights they hand it: constant under class
    confinement, period-varying under weight update. That is deliberate.
    If confinement aggregated levels and weight update chained relatives,
    the gap between them would mix the treatment with the aggregation form
    and would measure mostly the latter -- which is not the difference
    anyone is trying to see.

    Chained rather than a weighted mean of levels because weight update's
    weights move every period, and a weighted mean of levels under moving
    weights changes when the weights change even if no price does. Weighting
    the movements has the property that matters: a period in which nothing
    moved leaves the level exactly where it was, whatever happened to the
    weights.
    """
    columns = [c for c in indices.columns if c != "All items"]
    values = indices[columns].to_numpy(dtype=float)
    weights = weights_by_period.reindex(
        index=indices.index, columns=columns).to_numpy(dtype=float)
    levels = np.full(len(indices), np.nan)
    level = float(base_value)
    for t in range(len(indices)):
        if t == 0:
            levels[t] = level
            continue
        relatives = values[t] / values[t - 1]
        w = weights[t]
        usable = np.isfinite(relatives) & (relatives > 0) & np.isfinite(w) & (w > 0)
        if usable.any():
            level *= float((w[usable] * relatives[usable]).sum() / w[usable].sum())
        levels[t] = level
    return pd.Series(levels, index=indices.index, name="All items")


def class_confinement(df: pd.DataFrame, index_cfg: IndexConfig | None = None,
                      cfg: SeasonalConfig | None = None, *,
                      price_col: str = "price_imputed") -> SeasonalTreatmentResult:
    """The absent item's weight stays inside its class.

    The class index is computed over whichever of its items are in season --
    a matched comparison, so nothing about an item's arrival or departure is
    scored as price change -- and the class then enters the headline with
    its **full annual weight in every period**, in season or out. The basket
    never changes shape; what changes is which items inside a class are
    speaking for it.

    The assumption, stated plainly: that the in-season items of a class move
    the way the absent one would have. For strawberries against other soft
    fruit that is arguable. For a class whose only item is seasonal it is
    vacuous, and this returns that class holding its previous level, which
    is what a matched index does with nothing to match.

    CPI Manual 2020, Chapter 11, paras 11.10-11.18.
    """
    index_cfg = index_cfg or IndexConfig()
    cfg = cfg or SeasonalConfig()
    from .index import category_weights

    indices = _category_indices(df, index_cfg, price_col)
    weights = category_weights(df, "category")
    notes: list[str] = []
    seasonal = strictly_seasonal_items(df, cfg, price_col=price_col)

    # The class's *whole* weight, in every period, in season or out: that is
    # what confinement means. Built by the same function weight update uses,
    # handed an empty seasonal table so nothing is ever subtracted -- so the
    # two treatments provably start from one set of numbers and differ only
    # in what the season does to them.
    by_period = _in_season_weights(df, seasonal.iloc[:0], cfg,
                                   pd.DatetimeIndex(indices.index), list(indices.columns))
    if weights is None:
        notes.append(
            "the collection carries no expenditure weights, so each class counts once under "
            "confinement and by its in-season item count under weight update; the gap between "
            "the two is then about how many items were on sale rather than what they cost")

    if len(indices.columns) > 1:
        indices["All items"] = _chained_weighted_aggregate(indices, by_period,
                                                           index_cfg.base_value)
    else:
        indices["All items"] = indices.iloc[:, 0]

    items = tuple(seasonal.index[seasonal["strictly_seasonal"]].astype(str))
    if not items:
        notes.append(
            "no strictly seasonal item was found, so this treatment and the weight update "
            "treatment necessarily agree; the comparison below is not evidence that the "
            "choice does not matter, only that this collection does not exercise it")
    return SeasonalTreatmentResult(
        treatment="class_confinement", indices=indices, weights_by_period=by_period,
        seasonal_items=items, notes=tuple(notes))


def weight_update(df: pd.DataFrame, index_cfg: IndexConfig | None = None,
                  cfg: SeasonalConfig | None = None, *,
                  price_col: str = "price_imputed") -> SeasonalTreatmentResult:
    """The absent item's weight leaves the basket while it is absent.

    Each category's weight in a period is the weight of the items actually
    in season in that period, and the headline is a **chained** weighted
    mean of the category relatives using those period weights:

        I(t) = I(t-1) x [ sum_c w_c(t) I_c(t)/I_c(t-1) ] / sum_c w_c(t)

    Chained rather than a weighted mean of levels, deliberately. A weighted
    mean of levels with weights that change every period is not an index of
    anything: the level moves when the weights move even if no price does,
    which is the arithmetic error that makes time-varying weights look like
    inflation. Weighting the *movements* by the current period's weights is
    the standard construction and has the property that matters -- a period
    in which no price changes leaves the level exactly where it was.

    The assumption, stated plainly: that the absent item's expenditure
    simply did not happen. Where a household bought a substitute instead,
    this understates the basket the household actually faced, which is the
    price of not pretending to know what the substitute was.
    """
    index_cfg = index_cfg or IndexConfig()
    cfg = cfg or SeasonalConfig()

    indices = _category_indices(df, index_cfg, price_col)
    seasonal = strictly_seasonal_items(df, cfg, price_col=price_col)
    by_period = _in_season_weights(df, seasonal, cfg, pd.DatetimeIndex(indices.index),
                                   list(indices.columns))

    notes: list[str] = []
    if "weight" not in df.columns or not df["weight"].notna().any():
        notes.append(
            "the collection carries no expenditure weights, so each in-season item counts once "
            "and this treatment reweights categories by how many of their items were on sale")

    out = indices.copy()
    if len(indices.columns) > 1:
        out["All items"] = _chained_weighted_aggregate(indices, by_period, index_cfg.base_value)
    else:
        out["All items"] = indices.iloc[:, 0]

    items = tuple(seasonal.index[seasonal["strictly_seasonal"]].astype(str))
    if not items:
        notes.append(
            "no strictly seasonal item was found, so no weight ever leaves the basket and this "
            "treatment necessarily agrees with class confinement; that is a fact about this "
            "collection, not evidence that the choice does not matter")
    return SeasonalTreatmentResult(
        treatment="weight_update", indices=out, weights_by_period=by_period,
        seasonal_items=items, notes=tuple(notes))


@dataclass(frozen=True)
class TreatmentComparison:
    """Both strictly seasonal treatments, and what separates them."""

    results: Mapping[str, SeasonalTreatmentResult]
    table: pd.DataFrame
    """Per period, the headline under each treatment and the gap between
    them in index points."""
    max_gap_pp: float
    final_gap_pp: float
    seasonal_items: tuple[str, ...]
    identical: bool
    """True when the two treatments produced the same series to within
    rounding -- which happens when the collection has no strictly seasonal
    item, and means the comparison exercised nothing."""
    notes: tuple[str, ...] = ()


def compare_treatments(df: pd.DataFrame, index_cfg: IndexConfig | None = None,
                       cfg: SeasonalConfig | None = None, *,
                       price_col: str = "price_imputed") -> TreatmentComparison:
    """Compile both treatments on the same data and report the difference.

    This exists because the choice between them is a judgement about what a
    basket means while part of it is off the shelf, and a run that picked
    one silently has made that judgement on the reader's behalf without
    telling them. The gap is reported in index points, per period and at the
    end, in the same units as the headline it would change.
    """
    cfg = cfg or SeasonalConfig()
    results = {
        "class_confinement": class_confinement(df, index_cfg, cfg, price_col=price_col),
        "weight_update": weight_update(df, index_cfg, cfg, price_col=price_col),
    }
    confined = results["class_confinement"].headline
    updated = results["weight_update"].headline
    common = confined.index.intersection(updated.index)
    if len(common) == 0:
        raise SeasonalError(
            "the two treatments produced series with no period in common, which means one of "
            "them compiled nothing")
    table = pd.DataFrame({
        "class_confinement": confined.reindex(common),
        "weight_update": updated.reindex(common)})
    table["gap_pp"] = table["weight_update"] - table["class_confinement"]

    gaps = table["gap_pp"].abs().dropna()
    max_gap = float(gaps.max()) if len(gaps) else float("nan")
    final = table["gap_pp"].dropna()
    notes = tuple(dict.fromkeys(
        list(results["class_confinement"].notes) + list(results["weight_update"].notes)))
    return TreatmentComparison(
        results=results, table=table, max_gap_pp=max_gap,
        final_gap_pp=float(final.iloc[-1]) if len(final) else float("nan"),
        seasonal_items=results["class_confinement"].seasonal_items,
        identical=bool(np.isfinite(max_gap) and max_gap < 1e-9), notes=notes)


# ---------------------------------------------------------------------
# The Rothwell index
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class RothwellResult:
    index: pd.Series
    base_year: int
    base_prices: pd.Series
    """Each item's average price over the base year -- the denominator the
    whole index is built on."""
    items_in_base: int
    items_by_period: pd.Series
    notes: tuple[str, ...] = ()


def rothwell(df: pd.DataFrame, *, price_col: str = "price_imputed",
             base_year: int | None = None, base_value: float = 100.0,
             quantity_col: str | None = None) -> RothwellResult:
    """The Rothwell index: each month's prices against base-year average prices.

        P(t) = base_value x sum_{i in S(t)} q_i0 p_it / sum_{i in S(t)} q_i0 pbar_i0

    where `S(t)` is the set of items priced in period t, `pbar_i0` is item
    i's **average price over the whole base year**, and `q_i0` its base-year
    quantity (one, equally, when the collection carries none).

    The point of the construction is the denominator. A seasonal item has no
    single base-period price -- it has no price at all for part of the year --
    so an ordinary fixed-base index has nothing to divide by. Averaging over
    the base year gives every item one number that exists, and the index then
    asks the only question a seasonal basket can answer: what does what is on
    sale this month cost, relative to what those same things cost on average
    in the base year.

    What it is not is a pure price index. Because the item set changes with
    the season, a movement in this series mixes price change with the
    changing composition of the basket -- strawberry season shows up as a
    movement whether or not strawberries got dearer. That is inherent, it is
    why the index is confined to strictly seasonal classes in practice, and
    it is why `items_by_period` is returned alongside the levels.

    Rothwell (1958); ONS *CPI Technical Manual*, seasonal items.
    """
    required = {"period", "item_id"}
    if not required <= set(df.columns):
        raise SeasonalError(f"a Rothwell index needs {sorted(required)} columns")
    price = price_col if price_col in df.columns else "price_reported"

    frame = df.loc[:, ["period", "item_id"]].copy()
    frame["period"] = pd.to_datetime(frame["period"])
    frame["price"] = pd.to_numeric(df[price], errors="coerce").to_numpy()
    if quantity_col and quantity_col in df.columns:
        frame["quantity"] = pd.to_numeric(df[quantity_col], errors="coerce").to_numpy()
    else:
        from .index import quantity_series
        quantities = quantity_series(df, price)
        frame["quantity"] = (1.0 if quantities is None
                             else pd.to_numeric(quantities, errors="coerce").to_numpy())
    frame = frame[np.isfinite(frame["price"]) & (frame["price"] > 0)]
    if frame.empty:
        raise SeasonalError("no positive prices, so there is no Rothwell index to compute")

    years = sorted(frame["period"].dt.year.unique())
    chosen = int(base_year) if base_year is not None else int(years[0])
    if chosen not in years:
        raise SeasonalError(
            f"base year {chosen} is not present in the collection (years {years[0]}-{years[-1]})")
    base = frame[frame["period"].dt.year == chosen]
    base_prices = base.groupby("item_id")["price"].mean()
    base_quantities = base.groupby("item_id")["quantity"].mean().fillna(1.0)
    base_quantities = base_quantities.where(np.isfinite(base_quantities) & (base_quantities > 0),
                                            1.0)
    if base_prices.empty:
        raise SeasonalError(f"no priced item in base year {chosen}")

    levels: dict[pd.Timestamp, float] = {}
    counts: dict[pd.Timestamp, int] = {}
    notes: list[str] = []
    dropped: set[str] = set()
    for period, part in frame.groupby("period"):
        matched = part[part["item_id"].isin(base_prices.index)]
        dropped |= set(part.loc[~part["item_id"].isin(base_prices.index), "item_id"])
        if matched.empty:
            continue
        q = base_quantities.reindex(matched["item_id"]).to_numpy(dtype=float)
        pbar = base_prices.reindex(matched["item_id"]).to_numpy(dtype=float)
        current = matched["price"].to_numpy(dtype=float)
        denominator = float((q * pbar).sum())
        if denominator <= 0:
            continue
        levels[cast(pd.Timestamp, period)] = base_value * float((q * current).sum()) / denominator
        counts[cast(pd.Timestamp, period)] = int(len(matched))

    if not levels:
        raise SeasonalError(
            f"no period shares an item with base year {chosen}, so nothing can be compared "
            "against the base-year average prices")
    if dropped:
        notes.append(
            f"{len(dropped)} item(s) never appear in base year {chosen} and carry no base-year "
            "average price, so they are absent from every period of this index; a Rothwell "
            "index cannot price an item its base year never saw")

    return RothwellResult(
        index=pd.Series(levels, name="rothwell").sort_index(), base_year=chosen,
        base_prices=base_prices.sort_index(), items_in_base=int(len(base_prices)),
        items_by_period=pd.Series(counts, name="items").sort_index(), notes=tuple(notes))


# ---------------------------------------------------------------------
# Counter-seasonal estimation
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class CounterSeasonalResult:
    frame: pd.DataFrame
    """The panel with off-season cells filled, `price_imputed` carrying the
    estimate and `imputation` reading "counter_seasonal" on every row that
    got one."""
    estimated: int
    items: tuple[str, ...]
    coverage: pd.DataFrame
    """Per period, how many prices were estimated and what share of the
    period's observations they are."""
    notes: tuple[str, ...] = ()


def counter_seasonal_estimate(df: pd.DataFrame, cfg: SeasonalConfig | None = None, *,
                              price_col: str = "price_imputed") -> CounterSeasonalResult:
    """Estimate an out-of-season price by moving it with what is in season.

    The alternative to leaving a seasonal item out is to carry it through
    its off season at an estimated price, so the item never leaves the
    basket and its return is not scored as price change. What the estimate
    must not do is hold the price flat: a flat off-season price says the
    item's price did not move while everything around it did, and when the
    item reappears the whole accumulated difference arrives in one month.

    So the estimate is **counter-seasonal**: the item's last observed price
    is moved by the price movement of the items that *are* in season in its
    category, period by period across the gap. The imputed price therefore
    tracks general inflation through the off season and reappears in line
    with it.

    This is the class-mean rule of `engine.imputation` applied specifically,
    and only, to the off-season cells of strictly seasonal items, with the
    reference set restricted to items in season. It is separated from
    ordinary imputation because the two answer different questions: class
    mean fills a gap in a collection, this one constructs a price for a
    product that was not for sale. The second is a stronger claim and is
    labelled as its own method wherever it appears
    (`imputation == "counter_seasonal"`), never as an observation.

    CPI Manual 2020, Chapter 11: counter-seasonal imputation of off-season
    prices.
    """
    cfg = cfg or SeasonalConfig()
    price = price_col if price_col in df.columns else "price_reported"
    seasonal = strictly_seasonal_items(df, cfg, price_col=price)
    items = tuple(seasonal.index[seasonal["strictly_seasonal"]].astype(str))

    out = df.copy()
    out["period"] = pd.to_datetime(out["period"])
    if "imputation" not in out.columns:
        out["imputation"] = ""
    if price_col not in out.columns:
        out[price_col] = pd.to_numeric(out[price], errors="coerce")
    if not items:
        return CounterSeasonalResult(
            frame=out, estimated=0, items=(),
            coverage=pd.DataFrame(columns=["estimated", "observations", "share"]),
            notes=("no strictly seasonal item was found, so nothing was estimated",))

    periods = pd.DatetimeIndex(sorted(out["period"].unique()))
    values = pd.to_numeric(out[price_col], errors="coerce")
    prices = out.assign(_p=values.to_numpy()).pivot_table(
        index="period", columns="item_id", values="_p", aggfunc="first").reindex(periods)
    category_of = out.drop_duplicates("item_id").set_index("item_id")["category"] \
        if "category" in out.columns else pd.Series("all", index=prices.columns)

    # The in-season movement of each category, period on period: the
    # geometric mean relative over items priced in both periods. Exactly the
    # matched Jevons link the index itself uses, so the estimate moves the
    # way the category's index moves.
    movement: dict[str, pd.Series] = {}
    for category in sorted(set(category_of.reindex(prices.columns).dropna())):
        members = [c for c in prices.columns if category_of.get(c) == category]
        block = prices[members]
        ratios = block / block.shift(1)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            logs = pd.DataFrame(np.log(ratios.where(ratios > 0).to_numpy(dtype=float)),
                                index=prices.index, columns=block.columns)
            movement[str(category)] = pd.Series(
                np.exp(logs.mean(axis=1).to_numpy(dtype=float)), index=prices.index)

    estimates: dict[tuple[pd.Timestamp, str], float] = {}
    for item in items:
        if item not in prices.columns:
            continue
        column = prices[item]
        category = str(category_of.get(item, "all"))
        link = movement.get(category)
        if link is None:
            continue
        last: float | None = None
        for period in periods:
            observed = float(column.loc[period])
            if np.isfinite(observed) and observed > 0:
                last = observed
                continue
            if last is None:
                continue                # nothing observed yet to carry forward
            step = float(link.loc[period]) if period in link.index else float("nan")
            if not np.isfinite(step) or step <= 0:
                continue                # no in-season movement to borrow: leave the gap
            last = last * step
            estimates[(period, item)] = last

    if estimates:
        key = list(zip(out["period"], out["item_id"].astype(str), strict=True))
        filled = np.array([estimates.get(k, np.nan) for k in key])
        target = pd.to_numeric(out[price_col], errors="coerce").to_numpy(dtype=float)
        take = np.isfinite(filled) & ~(np.isfinite(target) & (target > 0))
        out.loc[take, price_col] = filled[take]
        out.loc[take, "imputation"] = "counter_seasonal"

    # Rows for an off-season cell may simply not exist in a long panel, in
    # which case the estimate has nowhere to land and is added as a new row:
    # the whole point is that the item stays in the basket.
    existing = set(zip(out["period"], out["item_id"].astype(str), strict=True))
    missing = [(p, i) for (p, i) in estimates if (p, i) not in existing]
    if missing:
        template = out.drop_duplicates("item_id").set_index("item_id")
        rows = []
        for period, item in missing:
            if item not in template.index:
                continue
            row = template.loc[item].to_dict()
            row.update({"period": period, "item_id": item, price_col: estimates[(period, item)],
                        "imputation": "counter_seasonal"})
            rows.append(row)
        if rows:
            out = pd.concat([out, pd.DataFrame(rows)], ignore_index=True)

    out = out.sort_values(["period", "item_id"]).reset_index(drop=True)
    flagged = out["imputation"].eq("counter_seasonal")
    coverage = pd.DataFrame({
        "estimated": flagged.groupby(out["period"]).sum().astype(int),
        "observations": out.groupby("period").size().astype(int)})
    coverage["share"] = coverage["estimated"] / coverage["observations"]
    return CounterSeasonalResult(
        frame=out, estimated=int(flagged.sum()), items=items, coverage=coverage,
        notes=("every estimated price is marked counter_seasonal and is a construction, not an "
               "observation; the share of each period built from them is above",))


# ---------------------------------------------------------------------
# Seasonal adjustment
# ---------------------------------------------------------------------
def x13_available() -> tuple[bool, str | None, str]:
    """Whether X-13ARIMA-SEATS can actually be run here, and where from.

    Returns (available, path, explanation). The explanation is written to be
    printed: when X-13 is absent, every output built on the fallback carries
    a sentence saying so, and that sentence is this one.

    Checked by looking for the executable, not by importing statsmodels'
    wrapper: the wrapper imports fine on a machine with no X-13 binary
    anywhere, and a check that passes on import would make the fallback
    silent, which is the exact failure this function exists to prevent.
    """
    for variable in ("X13PATH", "X12PATH"):
        configured = os.environ.get(variable)
        if configured:
            for name in ("x13as", "x13as.exe", "x12a", "x12a.exe"):
                candidate = os.path.join(configured, name)
                if os.path.isfile(candidate):
                    return True, configured, f"X-13ARIMA-SEATS found via {variable}"
            return (False, None,
                    f"{variable} is set to {configured!r} but holds no x13as or x12a "
                    "executable")
    for name in ("x13as", "x12a"):
        found = shutil.which(name)
        if found:
            return True, os.path.dirname(found), f"X-13ARIMA-SEATS found on PATH at {found}"
    return (False, None,
            "X-13ARIMA-SEATS is not installed on this machine: no x13as or x12a executable on "
            "PATH, and neither X13PATH nor X12PATH is set")


@dataclass(frozen=True)
class StabilityReport:
    """Evidence that the adjustment found a season rather than fitted noise."""

    factor_ranges: pd.Series
    """Per calendar period, the spread of the estimated seasonal factor
    across sub-samples, in percentage points. A factor that moves a great
    deal between halves of the sample is not a seasonal pattern."""
    max_factor_range_pct: float
    sub_samples: int
    trend_unadjusted_pct_per_year: float
    trend_adjusted_pct_per_year: float
    trend_difference_pp: float
    """The spurious trend: how far the adjusted series' annualised trend sits
    from the unadjusted series'. Seasonal factors that average out over a
    full year cannot change the trend, so anything materially non-zero here
    is the adjustment inventing or destroying growth."""
    stable: bool
    threshold_pct: float
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SeasonalAdjustment:
    """A seasonally adjusted series, and everything a reader needs with it."""

    unadjusted: pd.Series
    """Carried on the result itself, so no output can have the adjusted
    series without also having what it was adjusted from. That is not a
    convenience: an adjusted series published alone cannot be checked, and
    seasonal adjustment is where an unwanted movement is easiest to
    remove."""
    adjusted: pd.Series
    seasonal_factors: pd.Series
    trend: pd.Series | None
    engine: str
    """The engine that actually ran: "x13" or "stl"."""
    engine_requested: str
    fallback_reason: str | None
    """Why the engine that ran is not the one asked for, or None."""
    series_name: str
    periods_per_year: int
    stability: StabilityReport
    diagnostics: Mapping[str, float] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    approach: str = "direct"
    """"direct": this series itself was adjusted. "indirect" would be an
    aggregate assembled from separately adjusted components. The platform
    adjusts directly and only directly, and says which in every output,
    because the two give different answers and neither is wrong."""

    @property
    def fell_back(self) -> bool:
        return self.fallback_reason is not None

    @property
    def engine_label(self) -> str:
        return ENGINE_LABELS.get(self.engine, self.engine)

    @property
    def additivity_note(self) -> str:
        """Direct or indirect, and what that means for the parts.

        A directly adjusted total and the separately adjusted components
        beneath it are estimated independently, so the components need not
        add up to the total, and the gap is not an error. Offices that
        publish both either constrain the components (benchmarking) or
        publish the gap; this platform does neither, so it says so rather
        than letting a reader sum adjusted components and wonder why they
        miss.
        """
        return (f"adjusted directly (the {self.series_name} series itself, not an aggregate of "
                "separately adjusted components), so seasonally adjusted components need not "
                "sum to an adjusted total and no constraint forcing them to has been applied")

    @property
    def label(self) -> str:
        """The phrase every output prints beside the adjusted series.

        It names the engine unconditionally -- not only when the fallback
        ran -- because a reader cannot be expected to infer from silence
        that X-13 was used. When the fallback ran it says so in the same
        breath, so there is no way to quote the first half without the
        second. It ends with the additivity statement, for the same reason:
        one phrase, travelling everywhere, rather than a second phrase a
        format could forget.
        """
        if self.fell_back:
            return (f"seasonally adjusted with {self.engine_label}, not X-13ARIMA-SEATS "
                    f"({self.fallback_reason}); {self.additivity_note}")
        return f"seasonally adjusted with {self.engine_label}; {self.additivity_note}"

    @property
    def frame(self) -> pd.DataFrame:
        """Both series side by side, which is how they are published."""
        data = {"unadjusted": self.unadjusted, "adjusted": self.adjusted,
                "seasonal_factor": self.seasonal_factors}
        if self.trend is not None:
            data["trend"] = self.trend
        return pd.DataFrame(data)


def _annualised_trend_pct(series: pd.Series) -> float:
    """Percent per year implied by a least-squares fit to the log levels.

    A regression rather than an endpoint ratio, because an endpoint ratio on
    a seasonal series measures the two months it happened to start and end
    in as much as it measures the trend, and comparing an adjusted and an
    unadjusted series by their endpoints would therefore find a difference
    where the adjustment had made none.
    """
    clean = series.dropna()
    clean = clean[clean > 0]
    if len(clean) < 3:
        return float("nan")
    index = pd.DatetimeIndex(clean.index)
    years = (index - index[0]).days / 365.25
    slope = np.polyfit(np.asarray(years, dtype=float),
                       np.log(clean.to_numpy(dtype=float)), 1)[0]
    return float((np.exp(slope) - 1.0) * 100.0)


def _normalise_factors(seasonal_log: np.ndarray, periods_per_year: int) -> np.ndarray:
    """Force the seasonal factors to average to one over every full year.

    STL does not constrain its seasonal component to be neutral over a
    cycle, so the component can carry a slow drift of its own -- and
    dividing it out then moves the trend of the adjusted series, which is
    precisely the thing seasonal adjustment must not do. Subtracting a
    centred moving average of one year's width removes any such drift and
    leaves the within-year shape untouched, which is the standard
    normalisation of seasonal factors and what X-13 achieves through its
    own filters.

    The ends of the series have no centred window; they take the nearest
    complete one, which is the ordinary end-point compromise and is why an
    adjusted series is revised as more data arrives.
    """
    drift = (pd.Series(seasonal_log)
             .rolling(periods_per_year, center=True, min_periods=1).mean()
             .to_numpy(dtype=float))
    normalised: np.ndarray = seasonal_log - drift
    return normalised


def _stl_decompose(series: pd.Series, periods_per_year: int, seasonal: int
                   ) -> tuple[pd.Series, pd.Series, pd.Series]:
    from statsmodels.tsa.seasonal import STL

    values = series.to_numpy(dtype=float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fitted = STL(np.log(values), period=periods_per_year, seasonal=seasonal,
                     robust=True).fit()
    seasonal_log = _normalise_factors(np.asarray(fitted.seasonal, dtype=float),
                                      periods_per_year)
    factors = pd.Series(np.exp(seasonal_log), index=series.index, name="seasonal_factor")
    # The trend absorbs whatever the normalisation took out of the seasonal
    # component, so trend x seasonal still reconstructs the series.
    trend = pd.Series(np.exp(np.asarray(fitted.trend, dtype=float)
                             + np.asarray(fitted.seasonal, dtype=float) - seasonal_log),
                      index=series.index, name="trend")
    return series / factors, factors, trend


def _run_x13(series: pd.Series, path: str | None, periods_per_year: int
             ) -> tuple[pd.Series, pd.Series, pd.Series | None]:
    """One call into X-13ARIMA-SEATS, isolated so it can be replaced.

    A module-level function with a narrow contract rather than an inline
    call, so a machine without the binary can still exercise everything
    around it -- the labelling, the stability test, the outputs -- by
    substituting this one function. That matters more than it looks: the
    fallback path is the one that runs here, and a fallback whose
    alternative has never been executed is an assumption, not a branch.
    """
    from statsmodels.tsa.x13 import x13_arima_analysis

    frequency = {12: "ME", 4: "QE"}.get(periods_per_year)
    if frequency is None:
        raise SeasonalError(
            f"X-13ARIMA-SEATS handles monthly and quarterly data; this collection has "
            f"{periods_per_year} periods a year, so STL is the only engine available")
    endog = series.copy()
    endog.index = pd.DatetimeIndex(endog.index).to_period(
        "M" if periods_per_year == 12 else "Q")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = x13_arima_analysis(endog, x12path=path, outlier=True, print_stdout=False)
    adjusted = pd.Series(np.asarray(result.seasadj, dtype=float), index=series.index,
                         name=series.name)
    trend = (pd.Series(np.asarray(result.trend, dtype=float), index=series.index, name="trend")
             if getattr(result, "trend", None) is not None else None)
    factors = (series / adjusted).rename("seasonal_factor")
    return adjusted, factors, trend


def _seasonal_factors_only(series: pd.Series, periods_per_year: int, seasonal: int) -> pd.Series:
    """The seasonal factor per calendar period, averaged over the sample.

    Used by the stability test, which needs one number per calendar period
    per sub-sample. Always STL, even when the adjustment itself ran under
    X-13: the test is about whether the seasonal pattern in the *data* holds
    still across the sample, and running it under one estimator throughout
    is what makes its sub-samples comparable to each other.
    """
    _, factors, _ = _stl_decompose(series, periods_per_year, seasonal)
    return factors.groupby(season_of(pd.Series(series.index), periods_per_year).to_numpy()
                           ).mean()


def stability(series: pd.Series, cfg: SeasonalConfig, adjusted: pd.Series) -> StabilityReport:
    """Re-estimate the seasonal pattern on sub-spans and see whether it holds.

    Two questions, and they are different. Does the seasonal pattern stay
    the same across the sample -- or has the estimator fitted whatever each
    stretch happened to do? And has the adjustment introduced a trend that
    was not in the data? The second is the one that matters for publication:
    seasonal factors that average out over a full year cannot change a
    series' underlying growth, so a gap between the adjusted and unadjusted
    trends is the adjustment manufacturing or destroying growth, and it is
    reported in percentage points a year whether or not it is large.
    """
    notes: list[str] = []
    clean = series.dropna()
    clean = clean[clean > 0]
    per_sub = max(cfg.periods_per_year * 2, cfg.stl_seasonal + 1)
    possible = int(len(clean) // per_sub)
    sub_samples = max(0, min(cfg.stability_sub_samples, possible))

    ranges = pd.Series(dtype=float)
    if sub_samples >= 2:
        edges = np.linspace(0, len(clean), sub_samples + 1).astype(int)
        estimates = []
        for start, end in zip(edges[:-1], edges[1:], strict=True):
            chunk = clean.iloc[start:end]
            try:
                estimates.append(_seasonal_factors_only(chunk, cfg.periods_per_year,
                                                        cfg.stl_seasonal))
            except (ValueError, np.linalg.LinAlgError) as exc:      # pragma: no cover - guard
                notes.append(f"a sub-sample could not be decomposed: {exc}")
        if len(estimates) >= 2:
            wide = pd.DataFrame(estimates).T.dropna()
            ranges = ((wide.max(axis=1) - wide.min(axis=1)) * 100.0).rename("factor_range_pct")
    else:
        notes.append(
            f"the series spans {len(clean)} periods, too few to split into "
            f"{cfg.stability_sub_samples} sub-samples of at least {per_sub}; the seasonal "
            "pattern's stability across the sample has not been tested")

    unadjusted_trend = _annualised_trend_pct(clean)
    adjusted_trend = _annualised_trend_pct(adjusted)
    difference = (adjusted_trend - unadjusted_trend
                  if np.isfinite(unadjusted_trend) and np.isfinite(adjusted_trend)
                  else float("nan"))
    max_range = float(ranges.max()) if len(ranges) else float("nan")
    stable = bool(len(ranges) and max_range <= cfg.stability_threshold_pct)
    return StabilityReport(
        factor_ranges=ranges, max_factor_range_pct=max_range, sub_samples=len(ranges.index) and
        sub_samples, trend_unadjusted_pct_per_year=unadjusted_trend,
        trend_adjusted_pct_per_year=adjusted_trend, trend_difference_pp=difference,
        stable=stable, threshold_pct=cfg.stability_threshold_pct, notes=tuple(notes))


def adjust(series: pd.Series, cfg: SeasonalConfig | None = None, *,
           series_name: str | None = None) -> SeasonalAdjustment:
    """Seasonally adjust one index series, and say what did it.

    `adjustment_engine` is a request, not a guarantee, and the difference is
    the whole reason this returns an object rather than a series. "x13"
    demands X-13ARIMA-SEATS and raises where it is absent, for a caller who
    would rather have no adjusted series than one produced by something
    else. "auto" prefers X-13 and falls back to STL, recording in
    `fallback_reason` exactly why, which then appears in `label` and
    therefore in every output. "stl" asks for STL and gets it, with no
    fallback to record.

    The unadjusted series comes back on the same object. Everything that
    publishes an adjusted series publishes both, and this is the mechanism:
    there is no adjusted series available anywhere that is not accompanied
    by what it was adjusted from.
    """
    cfg = cfg or SeasonalConfig()
    clean = series.dropna()
    clean = clean[clean > 0]
    minimum = cfg.periods_per_year * 2
    if len(clean) < minimum:
        raise SeasonalError(
            f"seasonal adjustment needs at least two full years ({minimum} periods) to see a "
            f"pattern repeat; this series has {len(clean)}. A 'seasonal factor' estimated from "
            "one cycle is the cycle itself, and subtracting it would leave nothing.")

    warnings_: list[str] = []
    requested = cfg.adjustment_engine
    available, path, explanation = x13_available()
    fallback_reason: str | None = None

    if requested == "x13" and not available:
        raise SeasonalError(
            f"adjustment_engine is 'x13' and {explanation}. Install X-13ARIMA-SEATS and point "
            "X13PATH at it, or choose 'auto' to fall back to STL -- which will be labelled as "
            "STL in every output, because it is not the same method.")

    engine = "stl"
    adjusted: pd.Series
    factors: pd.Series
    trend: pd.Series | None
    if requested in ("x13", "auto") and available:
        try:
            adjusted, factors, trend = _run_x13(clean, path, cfg.periods_per_year)
            engine = "x13"
        except Exception as exc:            # noqa: BLE001 - any X-13 failure is a fallback
            if requested == "x13":
                raise SeasonalError(
                    f"X-13ARIMA-SEATS was found but failed on this series: {exc}") from exc
            fallback_reason = f"X-13ARIMA-SEATS was found but failed on this series: {exc}"
            adjusted, factors, trend = _stl_decompose(clean, cfg.periods_per_year,
                                                      cfg.stl_seasonal)
    else:
        if requested == "auto":
            fallback_reason = explanation
        adjusted, factors, trend = _stl_decompose(clean, cfg.periods_per_year, cfg.stl_seasonal)

    report = stability(clean, cfg, adjusted)
    if not report.stable and len(report.factor_ranges):
        warnings_.append(
            f"the seasonal factors move by up to {report.max_factor_range_pct:.2f} points "
            f"between sub-samples, above the {cfg.stability_threshold_pct:g} point threshold: "
            "the pattern being removed is not stable across this sample, and an adjusted "
            "series built on it will be revised when more data arrives")
    if np.isfinite(report.trend_difference_pp) and abs(report.trend_difference_pp) > 0.1:
        warnings_.append(
            f"the adjusted series' trend ({report.trend_adjusted_pct_per_year:.2f}% a year) "
            f"differs from the unadjusted series' ({report.trend_unadjusted_pct_per_year:.2f}% "
            f"a year) by {report.trend_difference_pp:+.2f} points. Seasonal factors that "
            "average out over a year cannot move a trend, so this gap is the adjustment "
            "itself, not the prices")

    diagnostics = {
        "n_periods": float(len(clean)),
        "seasonal_amplitude_pct": float((factors.max() - factors.min()) * 100.0),
        "residual_sd_pct": float(np.std(np.log(
            (adjusted / trend).dropna().to_numpy(dtype=float))) * 100.0)
        if trend is not None else float("nan"),
    }
    return SeasonalAdjustment(
        unadjusted=clean.rename("unadjusted"), adjusted=adjusted.rename("adjusted"),
        seasonal_factors=factors.rename("seasonal_factor"), trend=trend, engine=engine,
        engine_requested=requested, fallback_reason=fallback_reason,
        series_name=series_name or str(series.name or "index"),
        periods_per_year=cfg.periods_per_year, stability=report, diagnostics=diagnostics,
        warnings=tuple(warnings_))


# ---------------------------------------------------------------------
# The whole seasonal section of a run
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class SeasonalResult:
    """Everything the seasonal stage of a run produced."""

    items: pd.DataFrame
    comparison: TreatmentComparison | None
    adjustment: SeasonalAdjustment | None
    rothwell: RothwellResult | None
    counter_seasonal: CounterSeasonalResult | None
    notes: tuple[str, ...] = ()

    @property
    def engine_label(self) -> str:
        """What the method note and every export print for the engine, even
        when no adjustment was computed."""
        if self.adjustment is None:
            return "no seasonal adjustment was computed"
        return self.adjustment.label

    @property
    def seasonal_item_count(self) -> int:
        return int(self.items["strictly_seasonal"].sum()) if len(self.items) else 0


def run_seasonal(df: pd.DataFrame, indices: pd.DataFrame, cfg: SeasonalConfig | None = None,
                 index_cfg: IndexConfig | None = None, *,
                 price_col: str = "price_imputed") -> SeasonalResult:
    """The seasonal stage, as the pipeline runs it.

    Everything here is optional and everything that is skipped is skipped
    for a stated reason, collected in `notes`. A stage that quietly produced
    nothing would be indistinguishable from one that produced "no
    seasonality", and those are different findings.
    """
    cfg = cfg or SeasonalConfig()
    notes: list[str] = []
    items = strictly_seasonal_items(df, cfg, price_col=price_col)

    comparison: TreatmentComparison | None = None
    if cfg.treatment == "both":
        try:
            comparison = compare_treatments(df, index_cfg, cfg, price_col=price_col)
        except (SeasonalError, ValueError) as exc:
            notes.append(f"the treatment comparison could not be computed: {exc}")
    else:
        builder = class_confinement if cfg.treatment == "class_confinement" else weight_update
        single = builder(df, index_cfg, cfg, price_col=price_col)
        comparison = TreatmentComparison(
            results={cfg.treatment: single},
            table=pd.DataFrame({cfg.treatment: single.headline}),
            max_gap_pp=float("nan"), final_gap_pp=float("nan"),
            seasonal_items=single.seasonal_items, identical=False,
            notes=single.notes + (
                f"only the {TREATMENT_LABELS[cfg.treatment].lower()} treatment was computed, so "
                "the difference the other would have made is not shown; set treatment to "
                "'both' to see it",))

    adjustment: SeasonalAdjustment | None = None
    if cfg.adjust:
        column = cfg.adjustment_series
        if column not in indices.columns:
            alternatives = ", ".join(map(str, indices.columns[:6]))
            notes.append(
                f"no column named {column!r} in the compiled index, so nothing was adjusted "
                f"(available: {alternatives})")
        else:
            try:
                adjustment = adjust(indices[column], cfg, series_name=str(column))
            except SeasonalError as exc:
                notes.append(f"seasonal adjustment was not computed: {exc}")

    rothwell_result: RothwellResult | None = None
    if cfg.rothwell:
        try:
            rothwell_result = rothwell(df, price_col=price_col)
        except SeasonalError as exc:
            notes.append(f"the Rothwell index was not computed: {exc}")

    counter: CounterSeasonalResult | None = None
    if cfg.counter_seasonal:
        try:
            counter = counter_seasonal_estimate(df, cfg, price_col=price_col)
        except (SeasonalError, ValueError) as exc:
            notes.append(f"counter-seasonal estimation was not computed: {exc}")

    return SeasonalResult(items=items, comparison=comparison, adjustment=adjustment,
                          rothwell=rothwell_result, counter_seasonal=counter,
                          notes=tuple(notes))


def treatment_table(comparison: TreatmentComparison | None) -> pd.DataFrame:
    """The comparison as a table an export can print, empty when there is
    nothing to compare rather than absent."""
    if comparison is None:
        return pd.DataFrame(columns=["class_confinement", "weight_update", "gap_pp"])
    return comparison.table


def adjustment_note(result: SeasonalResult | None) -> str:
    """One paragraph for the method note, naming the engine that ran.

    Returns the empty string when no seasonal work was done, so a caller can
    concatenate it unconditionally; it never returns a sentence about
    adjustment that omits the engine.
    """
    if result is None:
        return ""
    parts: list[str] = []
    count = result.seasonal_item_count
    parts.append(
        f"{count} item(s) were classified as strictly seasonal -- off the shelf for part of "
        "every year, rather than merely interrupted." if count else
        "No item was classified as strictly seasonal.")
    if result.comparison is not None and len(result.comparison.results) > 1:
        gap = result.comparison.max_gap_pp
        parts.append(
            "Both strictly seasonal treatments were compiled. Class confinement keeps each "
            "class's full weight in every period and lets its in-season items carry it; weight "
            "update removes an absent item's weight from the basket and renormalises, so the "
            "basket's shape varies with what is on sale. "
            + (f"They differ by at most {gap:.2f} index points over this collection"
               if np.isfinite(gap) else "Their difference could not be quantified")
            + (", which is the size of a judgement that would otherwise have been made "
               "silently." if np.isfinite(gap) and gap > 1e-9 else
               "; this collection has no strictly seasonal item, so the choice did not bite "
               "here and that is a fact about the data, not about the methods."))
    if result.adjustment is not None:
        a = result.adjustment
        parts.append(
            f"The {a.series_name} series was {a.label}. "
            + ("X-13ARIMA-SEATS is what statistical offices use and what a reader assumes on "
               "seeing 'seasonally adjusted'; STL is a robust loess decomposition with no "
               "trading-day or holiday regressors, no outlier model and no ARIMA extension of "
               "the series ends, so it is named rather than implied. " if a.fell_back else "")
            + "The unadjusted series is published alongside it wherever it appears. "
            + (f"Across {a.stability.sub_samples} sub-samples the estimated seasonal factors "
               f"moved by at most {a.stability.max_factor_range_pct:.2f} points"
               if np.isfinite(a.stability.max_factor_range_pct) else
               "The sample was too short to test the factors' stability across sub-samples")
            + (f", and the adjusted series' trend sits {a.stability.trend_difference_pp:+.2f} "
               f"points a year from the unadjusted series'."
               if np.isfinite(a.stability.trend_difference_pp) else "."))
    elif result.notes:
        parts.append("No seasonally adjusted series was produced: " + "; ".join(result.notes) + ".")
    if result.rothwell is not None:
        r = result.rothwell
        parts.append(
            f"A Rothwell index was computed against base year {r.base_year}, pricing each "
            f"period's available items against their base-year average prices over "
            f"{r.items_in_base} items.")
    if result.counter_seasonal is not None and result.counter_seasonal.estimated:
        parts.append(
            f"{result.counter_seasonal.estimated:,} off-season prices were estimated "
            "counter-seasonally -- moved with their category's in-season items rather than held "
            "flat -- and each is marked as a construction, not an observation.")
    return " ".join(parts)


def available_treatments() -> Sequence[str]:
    return TREATMENTS
