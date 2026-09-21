"""Imputation for gaps that a matched index cannot simply skip.

Each method biases the index in a known direction, so the method is a stated
choice with a stated cost, not a default:

carry_forward  holds the last price. Understates change during the gap and
               produces a catch-up jump on resumption.
class_mean     moves the missing item by the average change of its priced
               peers. Assumes the gap is missing at random within the class,
               which is the standard assumption in official price statistics.
seasonal_hold  holds the index flat across an out-of-season gap, so the
               category contributes no change while unavailable. Equivalent to
               an all-seasonal treatment with no in-gap price movement.
"""

import warnings
from typing import Any, cast

import numpy as np
import pandas as pd

from ..core.config import ImputationConfig


def carry_forward(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["price_imputed"] = out.groupby("item_id")["price_clean"].ffill()
    out.loc[out["price_clean"].isna() & out["price_imputed"].notna(),
            "imputation"] = "carry_forward"
    return out


def class_mean(df: pd.DataFrame) -> pd.DataFrame:
    """Move an unpriced item by the geometric mean change of its category peers.

    Implemented on the log scale so the imputed movement is consistent with the
    Jevons aggregation used downstream. Applied period by period so the imputed
    value can itself carry forward through a multi-period gap.

    Pivoted to an item x period matrix once, and dropped to a plain numpy
    array immediately, rather than re-filtering the long frame by period
    inside the loop or writing into a DataFrame one row at a time. Both of
    those are cheap-looking single calls that carry real per-call overhead
    in pandas (block-manager housekeeping on every `.loc` write); paid once
    per period per category with a gap, on a multi-year monthly panel, that
    is the difference between a visible multi-second stall on upload and an
    unnoticeable one. The cascading fill itself is plain numpy row
    assignment, which is what a sequential, gap-carries-forward computation
    like this actually needs.
    """
    out = df.copy().sort_values(["item_id", "period"])
    out["price_imputed"] = out["price_clean"]

    fills = []
    for _cat, d in out.groupby("category"):
        pivot = d.pivot(index="period", columns="item_id", values="price_clean").sort_index()
        periods, items = pivot.index, pivot.columns
        arr = pivot.to_numpy(dtype=float)

        positive = np.where(arr > 0, arr, np.nan)
        with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
            # A transition with no item priced on both sides gives an all-NaN
            # row; nanmean's "empty slice" warning for that case is expected,
            # not a sign of a problem, so it is silenced rather than raised.
            warnings.filterwarnings("ignore", message="Mean of empty slice")
            class_factor = np.nanmean(np.diff(np.log(positive), axis=0), axis=1)

        filled = arr.copy()
        for i in range(1, len(periods)):
            factor = class_factor[i - 1]
            if not np.isfinite(factor):
                continue
            prev_row = filled[i - 1]
            fillable = np.isnan(filled[i]) & ~np.isnan(prev_row)
            if fillable.any():
                filled[i, fillable] = prev_row[fillable] * np.exp(factor)

        fills.append(pd.DataFrame(filled, index=periods, columns=items)
                     .reset_index()
                     .melt(id_vars="period", var_name="item_id", value_name="_filled")
                     .dropna(subset=["_filled"]))

    imputed_long = pd.concat(fills, ignore_index=True) if fills else pd.DataFrame(
        columns=["period", "item_id", "_filled"])
    out = out.merge(imputed_long, on=["period", "item_id"], how="left")
    fillable_mask = out["price_clean"].isna() & out["_filled"].notna()
    out.loc[fillable_mask, "price_imputed"] = out.loc[fillable_mask, "_filled"]
    out.loc[fillable_mask, "imputation"] = "class_mean"
    return out.drop(columns=["_filled"])


def seasonal_hold(df: pd.DataFrame) -> pd.DataFrame:
    """Leave the gap unfilled. The index module holds the level flat when no
    items match, which is the intended behaviour for an out-of-season period."""
    out = df.copy()
    out["price_imputed"] = out["price_clean"]
    out.loc[out["price_clean"].isna(), "imputation"] = "seasonal_hold"
    return out


def _matched_cell_level(prices: pd.DataFrame) -> pd.Series:
    """A chained level series for one cell, built only from items priced
    in both periods of each link.

    Matched, for the same reason every other comparison in this engine is:
    the mean price of whatever happened to be collected this period
    against the mean price of whatever happened to be collected last
    period measures the change in the *sample* as much as the change in
    prices. A cell whose cheap item went missing would show a price rise
    on that basis alone -- which, in an imputation routine, is exactly the
    wrong direction, since it would then impute that fictitious rise onto
    the missing item itself.

    `prices` is a period x item matrix. Returns a level series starting at
    1.0, holding its level across any link with no matched pair.
    """
    arr = prices.to_numpy(dtype=float)
    positive = np.where(arr > 0, arr, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Mean of empty slice")
        log_changes = np.nanmean(np.diff(np.log(positive), axis=0), axis=1)

    levels = np.ones(len(prices.index))
    for i, change in enumerate(log_changes, start=1):
        levels[i] = levels[i - 1] * (np.exp(change) if np.isfinite(change) else 1.0)
    return pd.Series(levels, index=prices.index)


def _impute_from_cell_level(
    df: pd.DataFrame, group_cols: list[str], method_name: str
) -> pd.DataFrame:
    """Fill each gap from the item's last *observed* price, carried along
    the matched level of its cell.

        p_imputed(t) = p_observed(t_last) * L(t) / L(t_last)

    Shared by `targeted_mean` (cell = the item's own category) and
    `overall_mean` (cell = the whole collection). Neither cascades: the
    input is always the last genuinely observed price and a level built
    only from observed matched pairs, so an imputed value is never an
    input to another imputation. `class_mean` deliberately does cascade,
    which is what lets it bridge long gaps; the cost is that a long gap
    there is carried on imputations compounding on imputations, and this
    is the conservative alternative.
    """
    out = df.copy().sort_values(["item_id", "period"]).reset_index(drop=True)
    out["price_imputed"] = out["price_clean"]
    if out["price_clean"].notna().sum() == 0:
        return out

    levels: list[pd.DataFrame] = []
    for key, d in (out.groupby(group_cols) if group_cols else [((), out)]):
        pivot = d.pivot_table(index="period", columns="item_id", values="price_clean",
                              aggfunc="first").sort_index()
        level = _matched_cell_level(pivot)
        frame = level.rename("_level").reset_index()
        if group_cols:
            values = key if isinstance(key, tuple) else (key,)
            for col, value in zip(group_cols, values, strict=True):
                frame[str(col)] = cast("Any", value)
        levels.append(frame)

    level_long = pd.concat(levels, ignore_index=True)
    out = out.merge(level_long, on=[*group_cols, "period"], how="left")

    # The level at the item's most recent *observed* price, carried
    # forward alongside that price so the two always refer to the same
    # period even across a multi-period gap.
    observed = out["price_clean"].notna()
    out["_last_price"] = out["price_clean"].where(observed).groupby(out["item_id"]).ffill()
    out["_last_level"] = out["_level"].where(observed).groupby(out["item_id"]).ffill()

    fillable = (~observed) & out["_last_price"].notna() & out["_last_level"].notna() \
        & out["_level"].notna() & (out["_last_level"] != 0)
    out.loc[fillable, "price_imputed"] = (
        out.loc[fillable, "_last_price"]
        * out.loc[fillable, "_level"] / out.loc[fillable, "_last_level"])
    out.loc[fillable, "imputation"] = method_name
    return out.drop(columns=["_level", "_last_price", "_last_level"])


def targeted_mean(df: pd.DataFrame) -> pd.DataFrame:
    """Move an unpriced item by the matched mean change of its own cell --
    its own category, in its own period.

    The distinction from `class_mean`, easy to miss because both are
    "impute from the peers": class_mean cascades, filling period by period
    so that an imputed value becomes an input to the next period's fill.
    An item missing for eight months is carried the whole way on
    imputations compounding on imputations. Targeted mean never does that:
    every fill is anchored to the item's last genuinely observed price.

    That makes it the more conservative of the two, and the one to prefer
    when gaps are short and scattered. Its cost is coverage -- a gap whose
    cell has no matched pairs is left unfilled rather than bridged, which
    shows up in `response_rates` instead of hiding inside a plausible
    looking series.

    CPI Manual 2020, Chapter 6: targeted (cell) mean imputation, as
    against overall mean imputation below.
    """
    return _impute_from_cell_level(df, ["category"], "targeted_mean")


def overall_mean(df: pd.DataFrame) -> pd.DataFrame:
    """Move an unpriced item by the matched mean change of every priced
    item in the collection, not only its own category's.

    The fallback when a category is too thin to speak for itself: where
    every item in a category is missing at once, its own cell has no
    matched pair to compute a change from, while the collection as a whole
    still does.

    Its assumption is correspondingly stronger and usually wrong -- that
    the missing item moves like the average of everything, across
    categories with nothing to do with each other. Reach for it when the
    alternative is leaving a materially weighted category unrepresented,
    and read the response rate before believing the result.

    CPI Manual 2020, Chapter 6: overall mean imputation.
    """
    return _impute_from_cell_level(df, [], "overall_mean")


def none(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["price_imputed"] = out["price_clean"]
    return out


METHODS = {"none": none, "carry_forward": carry_forward,
           "class_mean": class_mean, "seasonal_hold": seasonal_hold,
           "targeted_mean": targeted_mean, "overall_mean": overall_mean}


def response_rates(df: pd.DataFrame, by: str = "category") -> pd.DataFrame:
    """Observed, imputed and still-missing counts per group and period,
    with the response rate that summarises them.

    The number that has to travel with an index and usually does not. A
    category index resting on three observed prices and seven imputed ones
    is a much weaker claim than the same index resting on ten observed
    ones, and nothing in the index level itself distinguishes them --
    imputation is designed to be invisible in the output, which is exactly
    why it has to be counted here.

    `response_rate` is observed over expected (the rows the collection
    intended to price). `imputed_share` is the share of the cell that was
    filled rather than collected: the share of the aggregate that is
    model output rather than observation.
    """
    out = df.copy()
    if "imputation" not in out.columns:
        out["imputation"] = ""
    observed = out["price_clean"].notna()
    imputed = (~observed) & out.get(
        "price_imputed", pd.Series(np.nan, index=out.index)).notna()

    grouped = out.assign(_observed=observed, _imputed=imputed).groupby([by, "period"])
    table = grouped.agg(expected=("item_id", "size"), observed=("_observed", "sum"),
                        imputed=("_imputed", "sum"))
    table["still_missing"] = table["expected"] - table["observed"] - table["imputed"]
    table["response_rate"] = table["observed"] / table["expected"]
    table["imputed_share"] = table["imputed"] / table["expected"]
    return table


def imputation_summary(df: pd.DataFrame) -> pd.DataFrame:
    """One row per imputation method actually used, with how many values
    it produced and what share of all priced observations they are."""
    out = df.copy()
    if "imputation" not in out.columns:
        return pd.DataFrame(columns=["values", "share_of_priced"])
    used = out.loc[out["imputation"].fillna("").astype(str) != ""]
    priced = int(out.get("price_imputed", out["price_clean"]).notna().sum())
    counts = used.groupby("imputation").size().rename("values").to_frame()
    counts["share_of_priced"] = counts["values"] / priced if priced else np.nan
    return counts.sort_values("values", ascending=False)


def run_imputation(df: pd.DataFrame, cfg: ImputationConfig | None = None) -> pd.DataFrame:
    cfg = cfg or ImputationConfig()
    df = df.copy()
    df["imputation"] = ""

    for method in {cfg.method_for(cast(str, c)) for c in df["category"].unique()}:
        if method not in METHODS:
            raise ValueError(f"unknown imputation method '{method}'")

    # overall_mean is the one method whose whole point is that it looks
    # outside the category, so it cannot be run on a per-category slice
    # the way the others are: doing that would silently turn it into
    # targeted_mean, and it would fill nothing at all in exactly the case
    # it exists for -- a category with every item missing at once. It is
    # run against the entire collection, and only the rows belonging to
    # categories assigned to it are kept.
    overall_categories = {
        cast(str, c) for c in df["category"].unique()
        if cfg.method_for(cast(str, c)) == "overall_mean"}

    parts = []
    if overall_categories:
        collection_wide = overall_mean(df)
        parts.append(collection_wide[collection_wide["category"].isin(overall_categories)])

    for group_key, d in df.groupby("category"):
        # "category" is always string-typed in every DataFrame this runs on;
        # the cast records that, since pandas' groupby stub cannot know a
        # column's dtype ahead of time.
        cat = cast(str, group_key)
        if cat in overall_categories:
            continue
        method = cfg.method_for(cat)
        if method == "class_mean" and d["item_id"].nunique() <= 1:
            # class_mean moves an unpriced item by its peers' change; with a
            # single item there is no peer, so it would leave every gap
            # silently unfilled. carry_forward is the closest honest
            # approximation available, and it always sets the imputation
            # flag, so the audit trail never claims a fill that didn't happen.
            method = "carry_forward"
        parts.append(METHODS[method](d))
    return pd.concat(parts).sort_values(["item_id", "period"]).reset_index(drop=True)
