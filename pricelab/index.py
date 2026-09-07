"""Index number methods.

The elementary formulae are given with the property that justifies choosing
them, because in an index the formula is the methodological argument:

jevons     geometric mean of price relatives. Satisfies time reversal and is
           invariant to the units in which each item is quantified. The default
           for elementary aggregates in modern practice.
dutot      ratio of mean prices. Satisfies time reversal but is sensitive to
           the quantity unit of each item, so it is only safe over a
           homogeneous set of comparable items.
carli      arithmetic mean of price relatives. Fails time reversal and carries
           an upward bias. Included so the bias can be demonstrated, not
           recommended.
laspeyres  base-weighted arithmetic index, requires expenditure weights.

Matching is applied before any formula: only items priced in both periods enter
the comparison, so item replacement cannot be mistaken for price change.
"""

import numpy as np
import pandas as pd

from .config import IndexConfig


def _matched(a: pd.Series, b: pd.Series):
    common = a.index.intersection(b.index)
    a, b = a[common].dropna(), b[common].dropna()
    common = a.index.intersection(b.index)
    a, b = a[common], b[common]
    keep = (a > 0) & (b > 0)
    return a[keep], b[keep]


def jevons(a: pd.Series, b: pd.Series) -> float:
    a, b = _matched(a, b)
    if len(a) == 0:
        return np.nan
    return float(np.exp(np.log(b / a).mean()))


def dutot(a: pd.Series, b: pd.Series) -> float:
    a, b = _matched(a, b)
    if len(a) == 0:
        return np.nan
    return float(b.mean() / a.mean())


def carli(a: pd.Series, b: pd.Series) -> float:
    a, b = _matched(a, b)
    if len(a) == 0:
        return np.nan
    return float((b / a).mean())


def laspeyres(a: pd.Series, b: pd.Series, w: pd.Series = None) -> float:
    a, b = _matched(a, b)
    if len(a) == 0:
        return np.nan
    if w is None:
        return jevons(a, b)
    w = w.reindex(a.index).fillna(0.0)
    if w.sum() == 0:
        return jevons(a, b)
    return float((w * (b / a)).sum() / w.sum())


FORMULAE = {"jevons": jevons, "dutot": dutot, "carli": carli, "laspeyres": laspeyres}


def build_index(d: pd.DataFrame, cfg: IndexConfig = None,
                price_col: str = "price_imputed") -> pd.DataFrame:
    """Chained or fixed-base index for one group.

    Returns the index alongside the matched item count for each period, because
    an index built on two matched items is a different object from one built on
    six and the user needs to see which they have.
    """
    cfg = cfg or IndexConfig()
    fn = FORMULAE[cfg.formula]
    periods = sorted(d["period"].unique())
    series = {p: d.loc[d["period"] == p].set_index("item_id")[price_col] for p in periods}
    weights = None
    if "weight" in d.columns:
        weights = {p: d.loc[d["period"] == p].set_index("item_id")["weight"] for p in periods}

    base_period = pd.to_datetime(cfg.base_period) if cfg.base_period else periods[0]
    rows, level = [], cfg.base_value

    for i, p in enumerate(periods):
        if i == 0:
            matched = np.nan
        else:
            prev = periods[i - 1] if cfg.chained else base_period
            a, b = series[prev], series[p]
            am, bm = _matched(a, b)
            matched = len(am)
            if matched < cfg.min_matched_items:
                rel = np.nan            # hold the level, flagged below
            elif cfg.formula == "laspeyres" and weights is not None:
                rel = laspeyres(a, b, weights[prev])
            else:
                rel = fn(a, b)

            if cfg.chained:
                level = level * rel if np.isfinite(rel) else level
            else:
                level = cfg.base_value * rel if np.isfinite(rel) else np.nan

        rows.append({"period": p, "index": level, "matched_items": matched,
                     "insufficient_match": (not np.isnan(matched)) and matched < cfg.min_matched_items
                     if not isinstance(matched, float) or np.isfinite(matched) else False})

    out = pd.DataFrame(rows).set_index("period")

    # rebase so the chosen base period reads exactly base_value
    if cfg.chained and base_period in out.index and np.isfinite(out.loc[base_period, "index"]):
        out["index"] = out["index"] / out.loc[base_period, "index"] * cfg.base_value
    return out


def build_all(df: pd.DataFrame, cfg: IndexConfig = None,
              price_col: str = "price_imputed", group: str = "category"):
    """Index per group, plus an equally weighted aggregate.

    The aggregate is a geometric mean of the group indices. With no expenditure
    weights supplied this is indicative only, and the library says so rather
    than implying an authority it does not have.
    """
    cfg = cfg or IndexConfig()
    idx, matched = {}, {}
    for g, d in df.groupby(group):
        r = build_index(d, cfg, price_col)
        idx[g] = r["index"]
        matched[g] = r["matched_items"]
    I = pd.DataFrame(idx)
    M = pd.DataFrame(matched)
    if len(I.columns) > 1:
        I["All items"] = np.exp(np.log(I).mean(axis=1))
    return I, M


def year_on_year(I: pd.DataFrame, periods_per_year: int = 12) -> pd.DataFrame:
    return (I / I.shift(periods_per_year) - 1) * 100
