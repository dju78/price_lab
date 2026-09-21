"""Diagnostics.

The purpose here is to make method choices falsifiable. Any of these can be run
against a finished index to show what the choice cost, which is the difference
between asserting a method is right and demonstrating it.
"""

import numpy as np
import pandas as pd

from ..core.config import IndexConfig
from .index import build_all


def method_sensitivity(df: pd.DataFrame, cfg: IndexConfig | None = None,
                       formulae: tuple[str, ...] = ("jevons", "dutot", "carli"),
                       price_col: str = "price_imputed") -> pd.DataFrame:
    """Final index level under each elementary formula, same cleaned data.

    Turns a methodological preference into a quantified consequence.

    A run compiled on an analyst-defined formula gets an extra column for
    it, so the comparison answers the question that run actually raises:
    not just how the standard formulae differ from each other, but how far
    the custom one sits from all of them. `custom_formula` is cleared on
    each standard variant -- the question being asked of those is what the
    data looks like under Jevons, not under Jevons plus a stray
    expression that would never be read.
    """
    cfg = cfg or IndexConfig()
    out = {}
    for f in formulae:
        c = IndexConfig(**{**cfg.__dict__, "formula": f, "custom_formula": None})
        I, _ = build_all(df, c, price_col)
        out[f] = I.iloc[-1]
    if cfg.formula == "custom" and cfg.custom_formula:
        I, _ = build_all(df, cfg, price_col)
        out["custom"] = I.iloc[-1]
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


def churn(df: pd.DataFrame, price_col: str = "price_clean") -> tuple[pd.DataFrame, pd.DataFrame]:
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


def chain_drift(df: pd.DataFrame, cfg: IndexConfig | None = None,
                price_col: str = "price_imputed") -> pd.DataFrame:
    """Chained level against the direct fixed-base level at the final period.

    A large gap in a seasonal series is the classic warning that chaining is
    accumulating drift rather than measuring price change.
    """
    cfg = cfg or IndexConfig()
    from .splicing import chain_drift_table

    chained, _ = build_all(df, IndexConfig(**{**cfg.__dict__, "chained": True}), price_col)
    direct, _ = build_all(df, IndexConfig(**{**cfg.__dict__, "chained": False}), price_col)
    # The comparison itself is engine.splicing's: both series rebased to
    # their first common period, the gap in points and as a share of the
    # direct level, flagged against the run's configured threshold.
    table = chain_drift_table(chained, direct, threshold_pp=cfg.chain_drift_threshold_pp)
    out = pd.DataFrame({"chained": table["chained"], "fixed_base": table["direct"],
                        "drift_pp": table["drift_pp"], "drift_pct": table["drift_pct"],
                        "exceeds_threshold": table["exceeds_threshold"].astype(bool)})
    # A series the diagnostic could not compare (a direct index that is
    # all NaN because no item survives from the price reference to the
    # end) stays in the table with a NaN fixed base: that absence is
    # itself a finding the narrative reports, not a row to drop.
    out = out.reindex(chained.columns)
    out.index.name = None
    numeric = ["chained", "fixed_base", "drift_pp", "drift_pct"]
    out[numeric] = out[numeric].astype(float).round(2)
    out["chained"] = out["chained"].fillna(chained.iloc[-1].round(2))
    out["exceeds_threshold"] = out["exceeds_threshold"].fillna(False).astype(bool)
    return out
