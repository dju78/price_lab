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

from collections.abc import Mapping, Sequence
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

#: The quantity-weighted formulae (`engine.bilateral`, plus the unit value
#: index from `engine.elementary`) a run may name, with what each needs
#: beyond prices: quantities at the reference period, the current period,
#: or both. "laspeyres" is listed too because with quantities it is the
#: manual's quantity basket rather than the share-weighted Young form the
#: price-only path computes.
QUANTITY_FORMULAE: dict[str, tuple[str, ...]] = {
    "laspeyres": ("q0",),
    "paasche": ("qt",),
    "fisher": ("q0", "qt"),
    "tornqvist": ("q0", "qt"),
    "walsh": ("q0", "qt"),
    "marshall_edgeworth": ("q0", "qt"),
    "geometric_laspeyres": ("q0",),
    "geometric_paasche": ("qt",),
    "unit_value": ("q0", "qt"),
}
ELEMENTARY_FORMULAE_NAMES = ("jevons", "dutot", "carli")


def quantity_series(df: pd.DataFrame, price_col: str = "price_imputed") -> pd.Series | None:
    """The quantity behind each row, or None when the panel carries none.

    A `quantity` column is used as given. With only `expenditure`, quantity
    is derived as expenditure / price -- and the derivation is visible: the
    returned series is named "quantity_derived" rather than "quantity", so a
    result built on it says so. Where both exist, `quantity` wins and the
    disagreement, if any, is a validation finding, never resolved here.
    """
    if "quantity" in df.columns and df["quantity"].notna().any():
        return pd.to_numeric(df["quantity"], errors="coerce").rename("quantity")
    if "expenditure" in df.columns and df["expenditure"].notna().any():
        price = pd.to_numeric(df[price_col] if price_col in df.columns else df["price_reported"],
                              errors="coerce")
        with np.errstate(divide="ignore", invalid="ignore"):
            derived = pd.to_numeric(df["expenditure"], errors="coerce") / price
        return derived.where(np.isfinite(derived)).rename("quantity_derived")
    return None


def formula_availability(df: pd.DataFrame) -> dict[str, str | None]:
    """Every formula a run may name, mapped to None when the panel supports
    it or to the reason it does not. The interface shows the reason rather
    than hiding the formula, so a compiler learns what a Fisher needs
    instead of wondering where it went."""
    quantities = quantity_series(df)
    has_q = quantities is not None and quantities.notna().any()
    out: dict[str, str | None] = {name: None for name in ELEMENTARY_FORMULAE_NAMES}
    for name in QUANTITY_FORMULAE:
        if name == "laspeyres":
            out[name] = None    # always computable: weights or the Jevons fallback
            continue
        if has_q:
            out[name] = None
        else:
            out[name] = ("needs a quantity column (or expenditure, from which quantity is "
                         "derived) mapped on upload; this collection carries prices only"
                         + (" -- unit value also needs a homogeneity assertion" if name == "unit_value"
                            else ""))
    out["custom"] = None
    return out


def _bilateral_link(formula: str, a: np.ndarray, b: np.ndarray, qa: np.ndarray | None,
                    qb: np.ndarray | None, items: pd.Index,
                    justification: str | None) -> tuple[float, int]:
    """One quantity-weighted comparison, through `engine.bilateral` (or
    `engine.elementary.unit_value`), which is the tested implementation of
    each formula; this only adapts the period loop's arrays to it."""
    from . import bilateral
    from .elementary import unit_value as _unit_value

    p0, pt = pd.Series(a, index=items), pd.Series(b, index=items)
    q0 = pd.Series(qa, index=items) if qa is not None else None
    qt = pd.Series(qb, index=items) if qb is not None else None
    if formula == "unit_value":
        assert q0 is not None and qt is not None
        r = _unit_value(p0, pt, q0, qt, homogeneous=True,
                        homogeneity_justification=justification or "")
        return r.value, r.n_items
    needs = QUANTITY_FORMULAE[formula]
    args: list[pd.Series] = [p0, pt]
    if "q0" in needs:
        assert q0 is not None
        args.append(q0)
    if "qt" in needs:
        assert qt is not None
        args.append(qt)
    result = getattr(bilateral, formula)(*args)
    return result.value, result.n_items


def _custom_np(a: np.ndarray, b: np.ndarray, expression: str) -> float:
    """Evaluate an analyst-defined elementary formula over one matched
    comparison, from the numpy arrays the period loop already holds.

    The standard elementary indices are computed here and offered to the
    expression as names. Deliberately the same set, and the same
    arithmetic, as `engine.elementary` exposes to a caller working in
    pandas -- the difference is only that this path avoids constructing
    two Series per period per category, which on a multi-year panel is
    the difference between a custom formula being usable and being a
    thing people try once.
    """
    from ..core.security import evaluate_formula

    relatives = b / a
    carli_value = float(relatives.mean())
    harmonic_value = float(relatives.size / (1.0 / relatives).sum())
    variables = {
        "jevons": float(np.exp(np.log(relatives).mean())),
        "dutot": float(b.mean() / a.mean()),
        "carli": carli_value,
        "harmonic_mean": harmonic_value,
        "cswd": float(np.sqrt(carli_value * harmonic_value)),
        "n_items": float(relatives.size),
    }
    return float(evaluate_formula(expression, variables))


def _formula_np(
    formula: str, a: np.ndarray, b: np.ndarray, w: np.ndarray | None = None,
    expression: str | None = None,
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
    if formula == "custom":
        if not expression:
            raise ValueError(
                'formula is "custom" but no expression was supplied; IndexConfig rejects '
                "that combination, so reaching here means _formula_np was called directly "
                "without one")
        return _custom_np(am, bm, expression), n
    raise ValueError(f"unknown formula '{formula}'")


def resolve_index_reference_period(
    cfg: IndexConfig, index: Sequence[pd.Timestamp] | pd.Index
) -> pd.Timestamp:
    """The period a finished series is rebased to read `base_value` at.

    One resolution rule, shared by the arithmetic (`build_index`'s rebasing
    step) and by every label that describes it (the chart's y-axis, the
    deck's headline stat, the report's method note, the Findings metric),
    so a label can no longer claim a different reference period from the
    one the series was actually rebased to -- which is exactly the bug
    tests/test_deferred_deck_label.py was holding open.

    The fallback chain is `index_reference_period`, then
    `price_reference_period`, then the deprecated `base_period`, then the
    first period present. Defaulting to the price reference rather than
    straight to the first period matters once the two can differ: a
    fixed-base index compiled against a price reference reads `base_value`
    there by construction, and silently rebasing it onto its first period
    instead would renormalise away the very levels the caller asked for.
    """
    setting = cfg.index_reference_period or cfg.price_reference_period or cfg.base_period
    return pd.to_datetime(setting) if setting else pd.Timestamp(index[0])


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

    quantities = quantity_series(d, price_col)
    quantity_by_period: dict[pd.Timestamp, np.ndarray] | None = None
    use_quantities = cfg.formula in QUANTITY_FORMULAE and quantities is not None and (
        cfg.formula != "laspeyres" or quantities.notna().any())
    if cfg.formula in QUANTITY_FORMULAE and cfg.formula != "laspeyres" and quantities is None:
        raise ValueError(
            f"formula {cfg.formula!r} needs quantities and this collection carries none; "
            "map a quantity or expenditure column on upload, or choose an elementary formula")
    if use_quantities:
        assert quantities is not None
        qpivot = (d.assign(_q=quantities.to_numpy()).pivot(index="period", columns="item_id",
                                                             values="_q")
                  .reindex(columns=pivot.columns).sort_index())
        quantity_by_period = {
            p: qrow for p, qrow in zip(qpivot.index, qpivot.to_numpy(dtype=float), strict=True)}

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

    def quantity_row(p: pd.Timestamp) -> np.ndarray:
        return quantity_by_period.get(p, empty_row) if quantity_by_period is not None else empty_row

    def compare(p_from: pd.Timestamp, p_to: pd.Timestamp) -> tuple[float, int]:
        """The link (or fixed-base relative) from p_from to p_to, by the
        formula: the numpy elementary path as before, or the quantity-
        weighted path when the formula and the data call for it."""
        if use_quantities:
            return _bilateral_link(cfg.formula, row(p_from), row(p_to), quantity_row(p_from),
                                   quantity_row(p_to), pivot.columns, cfg.homogeneity_justification)
        return _formula_np(cfg.formula, row(p_from), row(p_to),
                           weight_row(p_from) if cfg.formula == "laspeyres" else None,
                           cfg.custom_formula)

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
        if cfg.chained:
            # A chained index's first period has no predecessor to link
            # from: it *is* the starting level, by construction.
            if i == 0:
                matched, insufficient = np.nan, False
            else:
                prev = periods[i - 1]
                rel, matched = compare(prev, p)
                insufficient = matched < cfg.min_matched_items
                if insufficient:
                    rel = np.nan        # hold the level, flagged below
                level = level * rel if np.isfinite(rel) else level
        else:
            # A fixed-base index compares every period, including the
            # first, against the price reference period. The first period
            # used to be hardcoded to `base_value` here alongside the
            # chained branch's genuine "no predecessor" case, which was
            # only ever harmless because nothing could set a price
            # reference away from the series' start: where it can (Lowe
            # and Young, this phase), that hardcode published a fabricated
            # 100 for a period whose real level relative to the price
            # reference is perfectly computable.
            rel, matched = compare(price_ref, p)
            insufficient = matched < cfg.min_matched_items
            if p == price_ref:
                # Definitionally base_value: an index at its own price
                # reference period is 100 whether or not enough items
                # matched, and NaN-ing it would take the rebasing step's
                # divisor with it.
                level = cfg.base_value
            elif insufficient or not np.isfinite(rel):
                level = np.nan
            else:
                level = cfg.base_value * rel

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
    index_ref = resolve_index_reference_period(cfg, out.index)
    if index_ref not in out.index:
        # Same rule as the price reference above: an explicitly requested
        # period that the data does not contain is an error, not a no-op.
        # Skipping the rebase here would leave the series at whatever level
        # the compilation produced while every label built from
        # `resolve_index_reference_period` (chart axis, deck headline,
        # report note) went on claiming it reads `base_value` at the
        # index reference period -- the label-versus-arithmetic split
        # this function was introduced to make impossible.
        raise ValueError(
            f"index_reference_period {index_ref:%Y-%m-%d} does not match any period present "
            f"in the data (available range: {periods[0]:%Y-%m-%d} to {periods[-1]:%Y-%m-%d}), "
            "so the series cannot be rebased to it. An un-rebased series published under a "
            "label naming that period would be mislabelled on every export.")
    base_level = cast(float, out.loc[index_ref, "index"])
    if not np.isfinite(base_level):
        # The period is present but the series has no level there (a
        # fixed-base comparison with too few items matched to the price
        # reference). A series that cannot be rebased to its index
        # reference period has no publishable levels at all: leaving it
        # un-rebased would put every label in the wrong, and raising would
        # take down diagnostics (`diagnostics.chain_drift` builds a direct
        # variant of every run) on thin categories that are an ordinary
        # fact of a collection. So the levels become NaN -- undefined, and
        # visibly so -- while the matched counts and insufficient-match
        # flags stay, because they are what explains the gap.
        out["index"] = np.nan
        return out
    out["index"] = out["index"] / base_level * cfg.base_value
    return out


def category_weights(df: pd.DataFrame, group: str = "category") -> dict[str, float] | None:
    """Expenditure weight per group from the panel's `weight` column: each
    item's weight (its mean over the periods it carries one) summed over
    the group. None when the panel has no usable weights, which is what
    makes the aggregate fall back to equal weighting."""
    if "weight" not in df.columns or not df["weight"].notna().any():
        return None
    per_item = df.dropna(subset=["weight"]).groupby([group, "item_id"])["weight"].mean()
    per_group = per_item.groupby(level=0).sum()
    weights = {str(k): float(v) for k, v in per_group.items() if np.isfinite(v) and v > 0}
    return weights or None


def build_all(df: pd.DataFrame, cfg: IndexConfig | None = None,
              price_col: str = "price_imputed", group: str = "category",
              parent_of: Mapping[str, str | None] | None = None,
              ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Index per group, plus the aggregate.

    The aggregate is the equally weighted geometric mean of the group
    indices when the panel carries no expenditure weights -- indicative
    only, and the library says so rather than implying an authority it
    does not have. With a `weight` column it is the weighted arithmetic
    mean (`engine.aggregation.weighted_aggregate`, whose contributions add
    up exactly), and with `parent_of` -- a classification tree the groups
    are codes in -- every parent node is rolled up too and the root is the
    aggregate. An analyst-defined aggregate formula, when configured,
    replaces the standard aggregate and marks the run non-standard.
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
        # Imported here rather than at module level because aggregation
        # imports data.classification for the weight-hierarchy rule, and
        # this module is imported by data/.
        from .aggregation import aggregate_tree, equally_weighted_aggregate, weighted_aggregate

        weights = category_weights(df, group)
        if weights is not None and parent_of is not None and all(c in parent_of for c in I.columns):
            rolled = aggregate_tree(I, weights, parent_of)
            for node in rolled.indices.columns:
                if node not in I.columns:
                    I[node] = rolled.indices[node]
            roots = [n for n, p in parent_of.items() if p is None and n in I.columns]
            I["All items"] = (I[roots[0]] if len(roots) == 1
                              else weighted_aggregate(I[roots], {r: float(rolled.weights.get(r, 0.0))
                                                                 for r in roots}))
        elif weights is not None:
            I["All items"] = weighted_aggregate(I, weights)
        else:
            I["All items"] = equally_weighted_aggregate(I)
        if cfg.custom_aggregate_formula:
            from .custom import evaluate_aggregate
            I["All items"] = evaluate_aggregate(cfg.custom_aggregate_formula, I)
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
