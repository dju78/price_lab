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

from typing import cast, overload

import numpy as np
import pandas as pd

from ..core.config import IndexConfig


def _matched(a: pd.Series, b: pd.Series) -> tuple[pd.Series, pd.Series]:
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


def laspeyres(a: pd.Series, b: pd.Series, w: pd.Series | None = None) -> float:
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


def _formula_np(
    formula: str, a: np.ndarray, b: np.ndarray, w: np.ndarray | None = None
) -> tuple[float, int]:
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


def build_index(d: pd.DataFrame, cfg: IndexConfig | None = None,
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
    # pandas' own stubs cannot know a column's dtype ahead of time, so
    # `.unique()` on a datetime64 column types as a broad Union rather than
    # pd.Timestamp; verified empirically that a datetime64 Series' `.unique()`,
    # once sorted, yields exactly pd.Timestamp at runtime, matching
    # `pivot.index`'s elements below, which is what the cast records.
    periods = cast(list[pd.Timestamp], sorted(d["period"].unique()))
    pivot = d.pivot(index="period", columns="item_id", values=price_col).sort_index()
    price_by_period = {p: row for p, row in zip(pivot.index, pivot.to_numpy(dtype=float), strict=True)}
    empty_row = np.full(pivot.shape[1], np.nan)

    weight_by_period: dict[pd.Timestamp, np.ndarray] | None = None
    if "weight" in d.columns:
        wpivot = (d.pivot(index="period", columns="item_id", values="weight")
                  .reindex(columns=pivot.columns).sort_index())
        weight_by_period = {
            p: row for p, row in zip(wpivot.index, wpivot.to_numpy(dtype=float), strict=True)}

    def row(p: pd.Timestamp) -> np.ndarray:
        return price_by_period.get(p, empty_row)

    def weight_row(p: pd.Timestamp) -> np.ndarray | None:
        return weight_by_period.get(p, empty_row) if weight_by_period is not None else None

    # The price reference period is the denominator of every price relative
    # in a fixed-base (non-chained) comparison; it has no bearing on a
    # chained index, where each link compares only to its immediate
    # predecessor. `price_reference_period` is the current name for this;
    # `base_period` is read as a fallback so a config that predates the
    # split (or was built without going through RunConfig.from_dict's
    # upconversion, e.g. constructed directly) still behaves exactly as it
    # always did.
    price_ref_setting = cfg.price_reference_period or cfg.base_period
    price_ref = pd.to_datetime(price_ref_setting) if price_ref_setting else periods[0]
    if price_ref_setting and price_ref not in price_by_period:
        # Name whichever field the caller actually set: a config built
        # before the three-period split (or constructed directly rather
        # than through RunConfig.from_dict's upconversion) set base_period,
        # and the error should say so rather than naming a field the
        # caller never touched.
        source = "price_reference_period" if cfg.price_reference_period else "base_period"
        raise ValueError(
            f"{source} {price_ref_setting!r} does not match any period present "
            f"in the data (available range: {periods[0]:%Y-%m-%d} to {periods[-1]:%Y-%m-%d}). "
            "Without a match the whole index would silently come back as all-NaN, which is "
            "worse than failing loudly.")
    rows, level = [], cfg.base_value

    for i, p in enumerate(periods):
        if i == 0:
            matched = np.nan
            insufficient = False
        else:
            prev = periods[i - 1] if cfg.chained else price_ref
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

    # Rebasing: a presentational rescaling of the finished series to read
    # base_value at the index reference period, applied identically
    # regardless of which method (chained or fixed-base) produced the
    # series. This is deliberately independent of `cfg.chained`: a fixed-
    # base index is not naturally at base_value at the index reference
    # period unless that period happens to equal the price reference
    # period, and forcing the two to coincide (by only ever rebasing the
    # chained branch) was the base_period conflation surviving in this one
    # branch after the rest of it was split out. Multiplying every level by
    # the same constant changes the level, never the ratio between any two
    # periods, so no period-on-period movement is affected by this step.
    index_ref_setting = cfg.index_reference_period or cfg.base_period
    index_ref = pd.to_datetime(index_ref_setting) if index_ref_setting else periods[0]
    if index_ref in out.index:
        base_level = cast(float, out.loc[index_ref, "index"])
        if np.isfinite(base_level):
            out["index"] = out["index"] / base_level * cfg.base_value
    return out


def build_all(df: pd.DataFrame, cfg: IndexConfig | None = None,
              price_col: str = "price_imputed", group: str = "category"
              ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Index per group, plus an equally weighted aggregate.

    The aggregate is a geometric mean of the group indices. With no expenditure
    weights supplied this is indicative only, and the library says so rather
    than implying an authority it does not have.
    """
    cfg = cfg or IndexConfig()
    idx: dict[str, pd.Series] = {}
    matched: dict[str, pd.Series] = {}
    for group_key, d in df.groupby(group):
        # `group` (the "category" column, by default) is always string-typed
        # in every DataFrame this runs on; the cast records that, since
        # pandas' groupby stub cannot know a column's dtype ahead of time
        # and so types its key as a broad, dtype-agnostic Union.
        g = cast(str, group_key)
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


@overload
def annualised_rate(level_ratio: float, years: float) -> float: ...
@overload
def annualised_rate(level_ratio: pd.Series, years: float) -> pd.Series: ...
def annualised_rate(level_ratio: float | pd.Series, years: float) -> float | pd.Series:
    """Percentage rate implied by a level ratio over a span in years.

    Takes and returns either a single ratio or a Series of them (one per
    category, as the Index build page's level table does in one call rather
    than looping), since the arithmetic below is identical either way; the
    two @overload signatures above let a caller's static type follow
    whichever one it actually passed in.

    Returns NaN rather than raising when the span is too short to annualise
    (a single-period collection has no rate to report), so callers degrade to
    "not available" instead of crashing on ZeroDivisionError.
    """
    if not years or years <= 0:
        return level_ratio * np.nan
    return (level_ratio ** (1 / years) - 1) * 100
