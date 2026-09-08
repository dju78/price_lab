"""Diagnostics.

The purpose here is to make method choices falsifiable. Any of these can be run
against a finished index to show what the choice cost, which is the difference
between asserting a method is right and demonstrating it.
"""

import numpy as np
import pandas as pd

from .config import IndexConfig
from .index import build_all


def method_sensitivity(df: pd.DataFrame, cfg: IndexConfig = None,
                       formulae=("jevons", "dutot", "carli"),
                       price_col: str = "price_imputed") -> pd.DataFrame:
    """Final index level under each elementary formula, same cleaned data.

    Turns a methodological preference into a quantified consequence.
    """
    cfg = cfg or IndexConfig()
    out = {}
    for f in formulae:
        c = IndexConfig(**{**cfg.__dict__, "formula": f})
        I, _ = build_all(df, c, price_col)
        out[f] = I.iloc[-1]
    res = pd.DataFrame(out)
    res["max_spread"] = res.max(axis=1) - res.min(axis=1)
    return res.round(2)


def unmatched_comparison(df: pd.DataFrame, I: pd.DataFrame,
                         price_col: str = "price_imputed") -> pd.DataFrame:
    """Matched index against a naive average of prices.

    The gap is the effect of item churn: where replacements enter dearer than
    the items they replace, the naive measure counts sample premiumisation as
    inflation. Where they enter cheaper, it understates.
    """
    naive = df.groupby(["category", "period"])[price_col].mean().unstack(0)
    # A zero base-period price (a mis-recoded sentinel, say) would otherwise
    # divide to +/-inf rather than NaN, and inf survives a plain .dropna()
    # downstream, reaching a published finding's text and its chart.
    base = naive.iloc[0].replace(0, np.nan)
    naive = naive / base * 100
    cats = [c for c in I.columns if c != "All items"]
    out = pd.DataFrame({
        "matched_index": I.iloc[-1].reindex(cats),
        "naive_mean_price": naive.iloc[-1].reindex(cats),
    })
    out["difference_pp"] = out["matched_index"] - out["naive_mean_price"]
    return out.round(1).sort_values("difference_pp")


def seasonality(I: pd.DataFrame, window: int = 13) -> pd.DataFrame:
    """Amplitude of the within-year cycle, measured on the index.

    Measured on the index rather than raw prices, because raw prices confound
    the seasonal cycle with changes in which items happen to be in the sample.
    """
    rows = []
    for c in I.columns:
        s = np.log(I[c]).dropna()
        if len(s) < window:
            continue
        detr = s - s.rolling(window, center=True, min_periods=window // 2).mean()
        m = detr.groupby(detr.index.month).mean()
        if m.isna().all():
            continue
        m = m - m.mean()
        rows.append({"series": c,
                     "amplitude_pct": 100 * (np.exp(m.max()) - np.exp(m.min())),
                     "peak_month": int(m.idxmax()), "trough_month": int(m.idxmin())})
    if not rows:
        # Every series ran shorter than `window` periods (a collection under
        # about a year old, or a single-period upload): there's no seasonal
        # cycle to measure yet. An empty frame with no "series" column would
        # make the caller's own set_index("series") raise; returning the
        # right (empty) shape here is what lets seasonal_findings' existing
        # `if not len(seas): return` handle this case as intended.
        return pd.DataFrame(columns=["amplitude_pct", "peak_month", "trough_month"]
                            ).rename_axis("series")
    return (pd.DataFrame(rows).set_index("series")
            .sort_values("amplitude_pct", ascending=False).round(1))


def churn(df: pd.DataFrame, price_col: str = "price_clean") -> pd.DataFrame:
    """Item lifespans and, more usefully, the price level at which replacements
    enter relative to those already in the sample."""
    d = df.dropna(subset=[price_col])
    life = (d.groupby(["category", "item_id", "item_name"])
              .agg(first=("period", "min"), last=("period", "max"),
                   periods=("period", "size"),
                   entry_price=(price_col, "first"), exit_price=(price_col, "last"))
              .reset_index())
    first_period = df["period"].min()
    entrants = life[life["first"] > first_period]
    summary = (life.groupby("category")
               .agg(items=("item_id", "nunique"),
                    median_lifespan=("periods", "median"),
                    min_lifespan=("periods", "min")))
    if len(entrants):
        summary["entrants_after_start"] = entrants.groupby("category").size()
    return summary.fillna(0), life


def chain_drift(df: pd.DataFrame, cfg: IndexConfig = None,
                price_col: str = "price_imputed") -> pd.DataFrame:
    """Chained level against the direct fixed-base level at the final period.

    A large gap in a seasonal series is the classic warning that chaining is
    accumulating drift rather than measuring price change.
    """
    cfg = cfg or IndexConfig()
    chained, _ = build_all(df, IndexConfig(**{**cfg.__dict__, "chained": True}), price_col)
    direct, _ = build_all(df, IndexConfig(**{**cfg.__dict__, "chained": False}), price_col)
    out = pd.DataFrame({"chained": chained.iloc[-1], "fixed_base": direct.iloc[-1]})
    out["drift_pp"] = out["chained"] - out["fixed_base"]
    return out.round(2)
