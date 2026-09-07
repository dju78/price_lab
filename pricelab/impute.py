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
    """
    out = df.copy().sort_values(["item_id", "period"])
    out["price_imputed"] = out["price_clean"]

    for cat, d in out.groupby("category"):
        periods = sorted(d["period"].unique())
        for t0, t1 in zip(periods[:-1], periods[1:]):
            a = d.loc[d["period"] == t0].set_index("item_id")
            b = d.loc[d["period"] == t1].set_index("item_id")
            # class movement from items priced in both periods
            both = a.index.intersection(b.index)
            rel = (b.loc[both, "price_clean"] / a.loc[both, "price_clean"]).dropna()
            rel = rel[rel > 0]
            if rel.empty:
                continue
            factor = float(np.exp(np.log(rel).mean()))

            need = b.index[b["price_clean"].isna()]
            prev = out.loc[(out["period"] == t0) & (out["item_id"].isin(need))] \
                      .set_index("item_id")["price_imputed"]
            fill = prev.dropna() * factor
            if fill.empty:
                continue
            mask = (out["period"] == t1) & (out["item_id"].isin(fill.index))
            out.loc[mask, "price_imputed"] = out.loc[mask, "item_id"].map(fill)
            out.loc[mask & out["price_clean"].isna(), "imputation"] = "class_mean"
            d = out.loc[out["category"] == cat]
    return out


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
