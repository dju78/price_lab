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

import numpy as np
import pandas as pd

from .config import ImputationConfig


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
    for cat, d in out.groupby("category"):
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


def none(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["price_imputed"] = out["price_clean"]
    return out


METHODS = {"none": none, "carry_forward": carry_forward,
           "class_mean": class_mean, "seasonal_hold": seasonal_hold}


def run_imputation(df: pd.DataFrame, cfg: ImputationConfig = None) -> pd.DataFrame:
    cfg = cfg or ImputationConfig()
    df = df.copy()
    df["imputation"] = ""
    parts = []
    for cat, d in df.groupby("category"):
        method = cfg.method_for(cat)
        if method not in METHODS:
            raise ValueError(f"unknown imputation method '{method}' for category '{cat}'")
        parts.append(METHODS[method](d))
    return pd.concat(parts).sort_values(["item_id", "period"]).reset_index(drop=True)
