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
    """Items priced, positive, in both periods.

    Indexing a Series with an Index object (`a[common]`) routes through
    pandas' generic listlike-indexer machinery, built for the general case of
    arbitrary, possibly-misaligned keys. That generality carries real
    per-call overhead, and this function is called once per period per
    category per formula: on a multi-year monthly panel run through several
    formulae for the sensitivity check, the overhead compounds into seconds.
    `a` and `b` already share an index in the common case (both are drawn
    from the same category's item x period pivot), so that case is handled
    with a plain boolean mask, which pandas executes on the underlying numpy
    array directly; only a genuine index mismatch pays for `reindex`.
    """
    if not a.index.equals(b.index):
        common = a.index.intersection(b.index)
        a, b = a.reindex(common), b.reindex(common)
    valid = a.notna().to_numpy() & b.notna().to_numpy() & (a.to_numpy() > 0) & (b.to_numpy() > 0)
    return a[valid], b[valid]


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


def _formula_np(formula: str, a: np.ndarray, b: np.ndarray, w: np.ndarray = None):
    """Numpy-array reimplementation of the formulae above, numerically
    identical to them, for the period loop in build_index.

    build_index is called once per formula in the sensitivity check and twice
    more for chain drift, each call stepping through every period of every
    category. `jevons`/`dutot`/`carli`/`laspeyres` above take pandas Series so
    they read as the methodological reference and so existing callers and
    tests keep working unchanged, but a pandas Series carries real per-call
    construction and alignment overhead. Paid once per period per category
    per formula, across a multi-year monthly panel, that overhead is exactly
    the delay a user watches on upload. This does the same arithmetic on the
    raw arrays instead, which is what the hot loop actually needs.
    """
    valid = np.isfinite(a) & np.isfinite(b) & (a > 0) & (b > 0)
    am, bm = a[valid], b[valid]
    n = int(am.size)
    if n == 0:
        return np.nan, n
    if formula == "jevons":
        return float(np.exp(np.log(bm / am).mean())), n
    if formula == "dutot":
        return float(bm.mean() / am.mean()), n
    if formula == "carli":
        return float((bm / am).mean()), n
    if formula == "laspeyres":
        if w is None:
            return float(np.exp(np.log(bm / am).mean())), n
        wm = np.nan_to_num(w[valid], nan=0.0)
        if wm.sum() == 0:
            return float(np.exp(np.log(bm / am).mean())), n
        return float((wm * (bm / am)).sum() / wm.sum()), n
    raise ValueError(f"unknown formula '{formula}'")


def build_index(d: pd.DataFrame, cfg: IndexConfig = None,
                price_col: str = "price_imputed") -> pd.DataFrame:
    """Chained or fixed-base index for one group.

    Returns the index alongside the matched item count for each period, because
    an index built on two matched items is a different object from one built on
    six and the user needs to see which they have.

    Prices are pivoted to an item x period matrix once, up front, and dropped
    to a plain numpy array immediately: the period loop then does pure numpy
    row lookups rather than pandas `.loc` calls. Re-filtering the long frame
    by period inside the loop, or even indexing a pandas Series once per
    period, is cheap in isolation but this function is called repeatedly
    (once per formula in the sensitivity check, twice more for chain drift),
    and on a multi-year monthly panel that per-call overhead compounds into a
    delay the user sits through on every upload.
    """
    cfg = cfg or IndexConfig()
    periods = sorted(d["period"].unique())
    pivot = d.pivot(index="period", columns="item_id", values=price_col).sort_index()
    price_by_period = {p: row for p, row in zip(pivot.index, pivot.to_numpy(dtype=float))}
    empty_row = np.full(pivot.shape[1], np.nan)

    weight_by_period = None
    if "weight" in d.columns:
        wpivot = (d.pivot(index="period", columns="item_id", values="weight")
                  .reindex(columns=pivot.columns).sort_index())
        weight_by_period = {p: row for p, row in zip(wpivot.index, wpivot.to_numpy(dtype=float))}

    def row(p):
        return price_by_period.get(p, empty_row)

    def weight_row(p):
        return weight_by_period.get(p, empty_row) if weight_by_period is not None else None

    # A category need not span the global base period (an item that launched
    # later never has). Falling back to an all-missing row, rather than
    # letting the lookup raise, is what lets a fixed-base comparison come back
    # as "impossible" (a NaN level) instead of crashing the whole index build.
    base_period = pd.to_datetime(cfg.base_period) if cfg.base_period else periods[0]
    if cfg.base_period and base_period not in price_by_period:
        raise ValueError(
            f"base_period {cfg.base_period!r} does not match any period present in the data "
            f"(available range: {periods[0]:%Y-%m-%d} to {periods[-1]:%Y-%m-%d}). Without a match "
            "the whole index would silently come back as all-NaN, which is worse than failing loudly.")
    rows, level = [], cfg.base_value

    for i, p in enumerate(periods):
        if i == 0:
            matched = np.nan
            insufficient = False
        else:
            prev = periods[i - 1] if cfg.chained else base_period
            rel, matched = _formula_np(cfg.formula, row(prev), row(p),
                                       weight_row(prev) if cfg.formula == "laspeyres" else None)
            insufficient = matched < cfg.min_matched_items
            if insufficient:
                rel = np.nan            # hold the level, flagged below

            if cfg.chained:
                level = level * rel if np.isfinite(rel) else level
            else:
                level = cfg.base_value * rel if np.isfinite(rel) else np.nan

        rows.append({"period": p, "index": level, "matched_items": matched,
                     "insufficient_match": insufficient})

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


def years_span(index: pd.DatetimeIndex) -> float:
    """Span of a period index, in years."""
    return (index[-1] - index[0]).days / 365.25


def annualised_rate(level_ratio, years: float):
    """Percentage rate implied by a level ratio over a span in years.

    Returns NaN rather than raising when the span is too short to annualise
    (a single-period collection has no rate to report), so callers degrade to
    "not available" instead of crashing on ZeroDivisionError.
    """
    if not years or years <= 0:
        return level_ratio * np.nan
    return (level_ratio ** (1 / years) - 1) * 100
