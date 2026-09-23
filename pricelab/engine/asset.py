"""Residential property price indices from transactions.

Five families, and they answer subtly different questions. The difference
is routinely misread as disagreement about one quantity, so every result
states what it measures and its principal limitation
(`PropertyIndexResult.measures`, `.limitation`, and `label`, which carries
both), and `compare_methods` explains the gaps between them with numbers
rather than laying the series side by side.

stratified median   the median sale price per stratum, re-weighted to a
                    fixed mix of strata. Rests on the strata: whatever
                    changes in the mix *within* a stratum is reported as
                    price change.
mix-adjusted mean   the mean price per cell (stratum x size band),
                    re-weighted to a fixed mix of cells. Finer than the
                    median, and the same limitation one level down: the
                    cells, and what is sold within them.
repeat sales        price change of the same property between its sales.
                    Bailey, Muth and Nourse (1963) by least squares;
                    Case and Shiller (1987) weighted, down-weighting pairs
                    with longer gaps between sales. Quality is held
                    constant by matching, but only for properties that sold
                    twice -- a selected sample -- and a property renovated
                    or run down between sales is treated as unchanged. Every
                    new period re-estimates the whole history: repeat sales
                    revises by construction (`repeat_sales_revisions`).
SPAR                sale price appraisal ratio: sale prices against a fixed
                    appraisal (a valuation roll) of the same properties.
                    Rests on the appraisal: its date, its quality and its
                    uniformity across the market.
hedonic             a time dummy regression of log price on characteristics,
                    by `engine/hedonic.fit_hedonic` -- the same estimator the
                    quality adjustment uses, not a second implementation.
                    Rests on the specification: a characteristic that
                    matters but is not in it leaves quality change in the
                    index.

Sources: Eurostat, *Handbook on Residential Property Prices Indices (RPPIs)*
(2013); Bailey, Muth and Nourse (1963); Case and Shiller (1987).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import hedonic as hd
from . import revision as rv

__all__ = [
    "METHODS",
    "METHOD_TEXT",
    "MethodComparison",
    "PropertyError",
    "PropertyIndexResult",
    "compare_methods",
    "hedonic_index",
    "market_coverage",
    "mix_adjusted_mean",
    "repeat_sales",
    "repeat_sales_pairs",
    "repeat_sales_revisions",
    "repeat_sales_vintages",
    "sale_price_appraisal_ratio",
    "stratified_median",
    "suppress_strata",
    "synthetic_market",
    "transaction_counts",
]

METHODS: tuple[str, ...] = ("stratified_median", "mix_adjusted_mean", "repeat_sales_bmn",
                            "repeat_sales_case_shiller", "spar", "hedonic")

METHOD_NAMES: dict[str, str] = {
    "stratified_median": "Stratified median",
    "mix_adjusted_mean": "Mix-adjusted mean",
    "repeat_sales_bmn": "Repeat sales (Bailey-Muth-Nourse)",
    "repeat_sales_case_shiller": "Repeat sales (Case-Shiller weighted)",
    "spar": "Sale price appraisal ratio (SPAR)",
    "hedonic": "Hedonic (time dummy)",
}

#: What each method measures, and the limitation that must be read with it.
METHOD_TEXT: dict[str, tuple[str, str]] = {
    "stratified_median": (
        "the change in the median sale price within each stratum, re-weighted to a fixed mix "
        "of strata",
        "it rests on the strata: a shift in what sells within a stratum -- more large houses, "
        "fewer small flats -- is reported as price change"),
    "mix_adjusted_mean": (
        "the change in the mean sale price within each cell (stratum by size band), "
        "re-weighted to a fixed mix of cells",
        "it rests on the cells: quality change within a cell is reported as price change, and "
        "a cell with no sales in the base period cannot enter"),
    "repeat_sales_bmn": (
        "the price change of the same properties between their sales, by least squares",
        "it rests on properties that sold at least twice, a selected sample; renovation and "
        "depreciation between sales are treated as no change; and every new period revises "
        "the whole history"),
    "repeat_sales_case_shiller": (
        "the price change of the same properties between their sales, with pairs whose sales "
        "are further apart weighted down because their errors are larger",
        "the same selected sample as any repeat sales index, and the same revision of history "
        "every period; the weighting depends on a second-stage model of the error variance"),
    "spar": (
        "the change in sale prices relative to a fixed appraisal of the same properties",
        "it rests on the appraisal: its date, its quality and whether it values all parts of "
        "the market alike"),
    "hedonic": (
        "the price change of a dwelling of fixed characteristics, read from a time dummy "
        "regression of log price on those characteristics",
        "it rests on the specification: a characteristic that matters but is not in the "
        "regression leaves its quality change in the index"),
}


class PropertyError(ValueError):
    """The transactions cannot support the index asked of them."""


@dataclass(frozen=True)
class PropertyIndexResult:
    method: str
    index: pd.Series
    """Base period = 100."""
    transactions: pd.Series
    """Transactions (or, for repeat sales, pairs) behind each period."""
    base: pd.Timestamp
    detail: pd.DataFrame | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def name(self) -> str:
        return METHOD_NAMES[self.method]

    @property
    def measures(self) -> str:
        return METHOD_TEXT[self.method][0]

    @property
    def limitation(self) -> str:
        return METHOD_TEXT[self.method][1]

    @property
    def label(self) -> str:
        """The name, what it measures and its limitation, in one phrase:
        printed wherever the number appears."""
        return (f"{self.name}, {self.base:%b %Y} = 100: measures {self.measures}. Limitation: "
                f"{self.limitation}.")


# ---------------------------------------------------------------------
# Preparation
# ---------------------------------------------------------------------
def _prepare(tx: pd.DataFrame, extra: Sequence[str] = ()) -> pd.DataFrame:
    needed = {"property_id", "period", "price", *extra}
    missing = sorted(needed - set(tx.columns))
    if missing:
        raise PropertyError(f"the transactions have no {missing} column(s)")
    frame = tx.dropna(subset=list(needed)).reset_index(drop=True).copy()
    frame["period"] = pd.DatetimeIndex(frame["period"])
    if (frame["price"] <= 0).any():
        raise PropertyError("sale prices must be positive")
    return frame


def _base(frame: pd.DataFrame, base: pd.Timestamp | None) -> pd.Timestamp:
    chosen = pd.Timestamp(base) if base is not None else pd.Timestamp(frame["period"].min())
    if not (frame["period"] == chosen).any():
        raise PropertyError(f"no sales in the base period {chosen:%Y-%m}")
    return chosen


def _fixed_mix_index(levels: pd.DataFrame, weights: pd.Series, base: pd.Timestamp
                     ) -> tuple[pd.Series, list[str]]:
    """sum_c w_c L_c(t) / L_c(base), over the cells present in t,
    renormalising the weights over them -- and saying so when it has to."""
    base_row = pd.Series(levels.loc[base], dtype=float)
    relatives = levels / base_row
    usable = [c for c in relatives.columns if np.isfinite(base_row[c])]
    w = weights.reindex(usable).fillna(0.0)
    values: dict[pd.Timestamp, float] = {}
    notes: list[str] = []
    for period in relatives.index:
        when = pd.Timestamp(period)
        row = pd.Series(relatives.loc[period, usable], dtype=float)
        present = row.notna() & (w > 0)
        if not present.any():
            values[when] = float("nan")
            continue
        if not present.all():
            notes.append(f"{when:%Y-%m}: {int((~present).sum())} cell(s) had no "
                         "sales; the rest were re-weighted over what sold")
        values[when] = float((row[present] * w[present]).sum() / w[present].sum() * 100.0)
    return pd.Series(values).sort_index(), notes


# ---------------------------------------------------------------------
# Stratified median and mix-adjusted mean
# ---------------------------------------------------------------------
def stratified_median(tx: pd.DataFrame, *, stratum_col: str = "stratum",
                      weights: Mapping[str, float] | None = None,
                      base: pd.Timestamp | None = None) -> PropertyIndexResult:
    """Median price per stratum per period, each stratum against its own
    base-period median, combined with fixed stratum weights -- the base
    period's value of sales by default, or `weights` if supplied (a dwelling
    stock, say)."""
    frame = _prepare(tx, (stratum_col,))
    b = _base(frame, base)
    grouped = frame.groupby(["period", stratum_col])["price"]
    medians = grouped.median().unstack()
    counts = grouped.size().unstack().fillna(0).astype(int)
    w = (pd.Series({str(k): float(v) for k, v in weights.items()}) if weights is not None else
         frame[frame["period"] == b].groupby(stratum_col)["price"].sum())
    medians.columns = medians.columns.astype(str)
    w.index = w.index.astype(str)
    index, notes = _fixed_mix_index(medians, w, b)
    detail = (medians / medians.loc[b] * 100.0)
    return PropertyIndexResult(
        method="stratified_median", index=index, transactions=counts.sum(axis=1), base=b,
        detail=pd.concat({"index": detail, "transactions": counts.rename(columns=str)}, axis=1),
        notes=tuple(notes))


def mix_adjusted_mean(tx: pd.DataFrame, *, stratum_col: str = "stratum",
                      size_col: str = "floor_area", bands: int = 3,
                      base: pd.Timestamp | None = None) -> PropertyIndexResult:
    """Mean price per cell (stratum by size band), each cell against its
    base-period mean, combined with the base period's value of sales per
    cell. Size bands are quantiles of `size_col` over the whole sample, so
    a cell means the same thing in every period."""
    frame = _prepare(tx, (stratum_col, size_col))
    b = _base(frame, base)
    frame["size_band"] = pd.qcut(frame[size_col], bands, labels=False, duplicates="drop")
    frame["cell"] = frame[stratum_col].astype(str) + " / band " + (frame["size_band"] + 1).astype(str)
    grouped = frame.groupby(["period", "cell"])["price"]
    means = grouped.mean().unstack()
    counts = grouped.size().unstack().fillna(0).astype(int)
    w = frame[frame["period"] == b].groupby("cell")["price"].sum()
    index, notes = _fixed_mix_index(means, w, b)
    missing = sorted(set(means.columns) - set(w.index))
    if missing:
        notes.append(f"{len(missing)} cell(s) had no base-period sales and cannot enter: "
                     f"{', '.join(missing[:4])}")
    return PropertyIndexResult(method="mix_adjusted_mean", index=index,
                               transactions=counts.sum(axis=1), base=b,
                               detail=(means / means.loc[b] * 100.0), notes=tuple(notes))


# ---------------------------------------------------------------------
# Repeat sales
# ---------------------------------------------------------------------
def repeat_sales_pairs(tx: pd.DataFrame) -> pd.DataFrame:
    """Consecutive sales of the same property, one row per pair. Two sales
    of one property in the same period carry no price change across time
    and are not paired."""
    frame = _prepare(tx).sort_values(["property_id", "period"])
    previous = frame.groupby("property_id")[["period", "price"]].shift(1)
    pairs = pd.DataFrame({
        "property_id": frame["property_id"], "first_period": previous["period"],
        "first_price": previous["price"], "second_period": frame["period"],
        "second_price": frame["price"]}).dropna()
    pairs = pairs[pairs["second_period"] > pairs["first_period"]]
    return pairs.reset_index(drop=True)


def repeat_sales(tx: pd.DataFrame, *, weighted: bool = False,
                 base: pd.Timestamp | None = None) -> PropertyIndexResult:
    """Repeat sales index.

    Bailey-Muth-Nourse: for each pair, ln(P2/P1) = beta(t2) - beta(t1) + e,
    beta(base) = 0, by least squares; the index is 100 exp(beta).

    Case-Shiller (`weighted=True`): the same regression in three stages. The
    squared residuals of the first are regressed on a constant and the
    number of periods between the sales, and the pairs re-weighted by the
    reciprocal of that fitted variance, since a pair whose sales are far
    apart has accumulated more property-specific drift. This is the
    geometric form; the arithmetic, value-weighted form estimated by
    instrumental variables is not implemented.
    """
    pairs = repeat_sales_pairs(tx)
    if pairs.empty:
        raise PropertyError("no property sold twice in different periods; a repeat sales "
                            "index has nothing to estimate from")
    frame = _prepare(tx)
    b = _base(frame, base)
    periods = sorted(set(pairs["first_period"]) | set(pairs["second_period"]) | {b})
    unknowns = [p for p in periods if p != b]
    column = {p: i for i, p in enumerate(unknowns)}
    X = np.zeros((len(pairs), len(unknowns)))
    for row, (first, second) in enumerate(zip(pairs["first_period"], pairs["second_period"],
                                              strict=True)):
        if first != b:
            X[row, column[first]] -= 1.0
        if second != b:
            X[row, column[second]] += 1.0
    y = np.log(pairs["second_price"].to_numpy(dtype=float)
               / pairs["first_price"].to_numpy(dtype=float))
    if np.linalg.matrix_rank(X) < X.shape[1]:
        raise PropertyError(
            "the pairs do not connect every period to the base: some period is linked to the "
            "base through no chain of repeat sales, so its level is not identified")
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    notes: list[str] = []
    method = "repeat_sales_bmn"
    if weighted:
        method = "repeat_sales_case_shiller"
        residuals = y - X @ beta
        gap = np.array([(s.year - f.year) * 12 + s.month - f.month for f, s in
                        zip(pairs["first_period"], pairs["second_period"], strict=True)],
                       dtype=float)
        Z = np.column_stack([np.ones_like(gap), gap])
        gamma, *_ = np.linalg.lstsq(Z, residuals ** 2, rcond=None)
        variance = Z @ gamma
        floor = max(float(np.mean(residuals ** 2)) * 1e-3, 1e-12)
        if (variance <= floor).any():
            notes.append(f"{int((variance <= floor).sum())} pair(s) had a non-positive fitted "
                         "error variance; it was floored before weighting")
        variance = np.maximum(variance, floor)
        scale = 1.0 / np.sqrt(variance)
        beta, *_ = np.linalg.lstsq(X * scale[:, None], y * scale, rcond=None)
        notes.append(f"error variance modelled as {gamma[0]:.5f} + {gamma[1]:.6f} x months "
                     "between sales")
    levels = {b: 100.0, **{p: 100.0 * float(np.exp(beta[column[p]])) for p in unknowns}}
    counts = pd.concat([pairs["first_period"], pairs["second_period"]]).value_counts()
    share = len(pairs) / max(len(frame), 1)
    notes.append(f"{len(pairs):,} pairs from {pairs['property_id'].nunique():,} properties; "
                 f"{share:.0%} of the {len(frame):,} sales are the second sale of a pair")
    return PropertyIndexResult(method=method, index=pd.Series(levels).sort_index(),
                               transactions=counts.sort_index(), base=b,
                               detail=pairs, notes=tuple(notes))


def repeat_sales_vintages(tx: pd.DataFrame, *, weighted: bool = False,
                          first_end: pd.Timestamp | None = None) -> list[rv.Vintage]:
    """The repeat sales index as it would have been published at the end
    of each period: the history re-estimated on the sales known by then.

    Returned as `engine.revision.Vintage` objects, so the revisions go
    through the same triangle, the same mean and mean absolute revision and
    the same bias test as every other revision in the platform.
    """
    frame = _prepare(tx)
    periods = sorted(frame["period"].unique())
    start = pd.Timestamp(first_end) if first_end is not None else pd.Timestamp(periods[2]) \
        if len(periods) > 2 else pd.Timestamp(periods[-1])
    out: list[rv.Vintage] = []
    for number, end in enumerate([pd.Timestamp(p) for p in periods if p >= start], start=1):
        known = frame[frame["period"] <= end]
        try:
            result = repeat_sales(known, weighted=weighted)
        except PropertyError:
            continue
        out.append(rv.Vintage(
            run_id=f"repeat-sales-to-{end:%Y-%m}", vintage=number, created_at=f"{end:%Y-%m-%d}",
            series=result.index, approved=True,
            label=f"{METHOD_NAMES[result.method]}, sales to {end:%b %Y}"))
    return out


def repeat_sales_revisions(tx: pd.DataFrame, *, weighted: bool = False,
                           first_end: pd.Timestamp | None = None) -> rv.RevisionAnalysis:
    """The revision profile repeat sales generates by construction, through
    `engine.revision.analyse` -- the same machinery as registry revisions."""
    vintages = repeat_sales_vintages(tx, weighted=weighted, first_end=first_end)
    if len(vintages) < 2:
        raise PropertyError("fewer than two vintages could be estimated, so nothing is revised")
    return rv.analyse(vintages, series_name=METHOD_NAMES["repeat_sales_case_shiller"
                                                         if weighted else "repeat_sales_bmn"])


# ---------------------------------------------------------------------
# SPAR and hedonic
# ---------------------------------------------------------------------
def sale_price_appraisal_ratio(tx: pd.DataFrame, *, appraisal_col: str = "appraisal",
                               base: pd.Timestamp | None = None) -> PropertyIndexResult:
    """(sum of sale prices / sum of their appraisals) in each period,
    against the same ratio in the base period -- the ratio of sums used by
    Statistics New Zealand and Statistics Netherlands."""
    frame = _prepare(tx, (appraisal_col,))
    if (frame[appraisal_col] <= 0).any():
        raise PropertyError("appraisals must be positive")
    b = _base(frame, base)
    sums = frame.groupby("period")[["price", appraisal_col]].sum()
    ratio = sums["price"] / sums[appraisal_col]
    total = len(tx)
    notes = (f"{len(frame):,} of {total:,} sales carry an appraisal and are used",)
    return PropertyIndexResult(method="spar", index=ratio / float(ratio[b]) * 100.0,
                               transactions=frame.groupby("period").size(), base=b,
                               notes=notes)


def hedonic_index(tx: pd.DataFrame, *, characteristics: Sequence[str] = ("floor_area",),
                  categorical: Sequence[str] = ("stratum",),
                  functional_form: str = "log_linear",
                  base: pd.Timestamp | None = None) -> PropertyIndexResult:
    """Time dummy hedonic index by `engine.hedonic.fit_hedonic`."""
    frame = _prepare(tx, (*characteristics, *categorical))
    b = _base(frame, base)
    spec = hd.HedonicSpec(characteristics=tuple(characteristics), categorical=tuple(categorical),
                          functional_form=functional_form, price_col="price",
                          period_col="period", item_col="property_id")
    try:
        fit = hd.fit_hedonic(frame, spec, time_dummies=True, base_period=b, cv_folds=0)
    except hd.HedonicError as exc:
        raise PropertyError(str(exc)) from exc
    notes = [f"specification: log price on {', '.join([*characteristics, *categorical])}; "
             f"adjusted R-squared {fit.adj_r_squared:.3f}", *fit.warnings]
    return PropertyIndexResult(method="hedonic", index=fit.time_dummy_index(),
                               transactions=frame.groupby("period").size(), base=b,
                               detail=fit.summary_frame(), notes=tuple(notes))


# ---------------------------------------------------------------------
# All five, and why they differ
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class MethodComparison:
    results: Mapping[str, PropertyIndexResult]
    table: pd.DataFrame
    quality_mix: pd.Series
    """The characteristics of what sold each period, valued at base-period
    hedonic prices, base = 100: how much 'more house' was sold."""
    explanation: tuple[str, ...]


def compare_methods(tx: pd.DataFrame, *, stratum_col: str = "stratum",
                    size_col: str = "floor_area", appraisal_col: str = "appraisal",
                    characteristics: Sequence[str] = ("floor_area",),
                    categorical: Sequence[str] = ("stratum",)) -> MethodComparison:
    """Every method on the same transactions, and the differences explained.

    The explanation is quantitative. The hedonic fit values each period's
    sold properties at base-period characteristic prices; the change in that
    value is the change in the quality mix of what sold, which the median
    and mean methods carry as price and the hedonic and repeat sales
    methods do not. The explanation states that mix change beside each
    method's gap to the hedonic index, so a reader can see how much of the
    gap it accounts for.
    """
    results: dict[str, PropertyIndexResult] = {
        "stratified_median": stratified_median(tx, stratum_col=stratum_col),
        "mix_adjusted_mean": mix_adjusted_mean(tx, stratum_col=stratum_col, size_col=size_col),
        "repeat_sales_bmn": repeat_sales(tx),
        "repeat_sales_case_shiller": repeat_sales(tx, weighted=True),
        "hedonic": hedonic_index(tx, characteristics=characteristics, categorical=categorical),
    }
    if appraisal_col in tx.columns and tx[appraisal_col].notna().any():
        results["spar"] = sale_price_appraisal_ratio(tx, appraisal_col=appraisal_col)
    table = pd.DataFrame({key: r.index for key, r in results.items()})
    frame = _prepare(tx, (*characteristics, *categorical))
    base = results["hedonic"].base

    # The quality mix of what sold, at base-period hedonic prices.
    spec = hd.HedonicSpec(characteristics=tuple(characteristics), categorical=tuple(categorical),
                          functional_form="log_linear", price_col="price", period_col="period",
                          item_col="property_id")
    fit = hd.fit_hedonic(frame, spec, time_dummies=True, base_period=base, cv_folds=0)
    predicted = np.log(fit.predict(frame[[*characteristics, *categorical]]))
    mix = np.exp(predicted.groupby(frame["period"]).mean())
    quality_mix = (mix / float(mix[base]) * 100.0).sort_index()

    last = table.dropna(how="all").index.max()
    hed = float(table.loc[last, "hedonic"])
    lines = [f"At {last:%b %Y}, on {len(frame):,} sales. The hedonic index holds the "
             f"characteristics fixed and reads {hed:.2f}."]
    drift = float(quality_mix[last]) - 100.0
    lines.append(
        f"What sold changed: valued at {base:%b %Y} characteristic prices, the properties sold "
        f"in {last:%b %Y} were worth {drift:+.2f}% more than those sold in {base:%b %Y}. A method "
        "that does not hold characteristics fixed carries that mix as if it were price.")
    # How much of that shift each group-based method actually carries: the
    # quality mix change *within* its own strata or cells, combined with the
    # same base-period value weights the method uses.
    frame["log_quality"] = predicted.to_numpy(dtype=float)
    frame["size_band"] = pd.qcut(frame[size_col], 3, labels=False, duplicates="drop")
    frame["cell"] = frame[stratum_col].astype(str) + " / band " + (
        frame["size_band"] + 1).astype(str)
    for key, group in (("stratified_median", stratum_col), ("mix_adjusted_mean", "cell")):
        within = _within_mix(frame, group, base, last)
        gap = float(table.loc[last, key]) - hed
        unit = "strata" if key == "stratified_median" else "cells (stratum by size band)"
        lines.append(
            f"{METHOD_NAMES[key]}: {float(table.loc[last, key]):.2f}, {gap:+.2f} points from the "
            f"hedonic index. It fixes the mix of {unit} but not what sells within them, and "
            f"within its {unit} the quality of what sold moved {within:+.2f}%, about "
            f"{hed * within / 100:+.2f} index points of the gap"
            + ("; a median also moves with the middle of each stratum's distribution rather than "
               "its mean, which accounts for more of it" if key == "stratified_median" else "")
            + ".")
    pairs = results["repeat_sales_bmn"].detail
    n_pairs = len(pairs) if pairs is not None else 0
    for key in ("repeat_sales_bmn", "repeat_sales_case_shiller"):
        gap = float(table.loc[last, key]) - hed
        lines.append(
            f"{METHOD_NAMES[key]}: {float(table.loc[last, key]):.2f}, {gap:+.2f} points from the "
            f"hedonic index. It rests on {n_pairs:,} pairs of sales of the same property "
            f"({n_pairs / max(len(frame), 1):.0%} of all sales), so quality is held by matching "
            "rather than by a model, for a sample that is not the whole market; its history "
            "will be revised as later sales arrive.")
    bmn, cs = (float(table.loc[last, k]) for k in ("repeat_sales_bmn",
                                                   "repeat_sales_case_shiller"))
    lines.append(f"The two repeat sales forms differ by {cs - bmn:+.2f} points: Case-Shiller "
                 "weights down pairs whose sales are far apart.")
    if "spar" in results:
        gap = float(table.loc[last, "spar"]) - hed
        lines.append(
            f"{METHOD_NAMES['spar']}: {float(table.loc[last, 'spar']):.2f}, {gap:+.2f} points from "
            "the hedonic index. It holds quality through each property's own appraisal, so it is "
            "only as even-handed as the appraisal is across the market.")
    spread = table.loc[last].max() - table.loc[last].min()
    lines.append(f"The methods span {spread:.2f} index points at {last:%b %Y}. They are not "
                 "estimates of one number; each answers the question stated beside it.")
    return MethodComparison(results=results, table=table, quality_mix=quality_mix,
                            explanation=tuple(lines))


def _within_mix(frame: pd.DataFrame, group: str, base: pd.Timestamp, last: pd.Timestamp
                ) -> float:
    """The change in the quality of what sold, within groups, in percent:
    sum_g w_g [exp(mean log quality_g(last) - mean log quality_g(base)) - 1],
    with w the base period's value of sales per group, over the groups that
    sold in both periods."""
    at = frame[frame["period"].isin([base, last])]
    means = at.groupby([group, "period"])["log_quality"].mean().unstack()
    both = means.dropna()
    if both.empty:
        return float("nan")
    weights = frame[frame["period"] == base].groupby(group)["price"].sum().reindex(both.index)
    change = np.exp(both[last] - both[base]) - 1.0
    return float((change * weights).sum() / weights.sum() * 100.0)


# ---------------------------------------------------------------------
# Diagnostics and suppression
# ---------------------------------------------------------------------
def transaction_counts(tx: pd.DataFrame, *, stratum_col: str = "stratum") -> pd.DataFrame:
    """Sales per stratum per period, with a total column."""
    frame = _prepare(tx, (stratum_col,))
    counts = frame.groupby(["period", stratum_col]).size().unstack().fillna(0).astype(int)
    counts.columns = counts.columns.astype(str)
    counts["All strata"] = counts.sum(axis=1)
    return counts


def market_coverage(tx: pd.DataFrame, *, stratum_col: str = "stratum",
                    stock: Mapping[str, float] | None = None,
                    appraisal_col: str = "appraisal",
                    characteristics: Sequence[str] = ("floor_area",)) -> pd.DataFrame:
    """How much of the market each method rests on.

    Per stratum: sales per period on average and, with a dwelling `stock`,
    the share of the stock that changes hands each period. And per method,
    as rows under "method": the share of sales it can use -- all of them
    for the median and mean, the second sales of pairs for repeat sales,
    those with an appraisal for SPAR, those with every characteristic for
    the hedonic.
    """
    frame = _prepare(tx, (stratum_col,))
    periods = frame["period"].nunique()
    per_stratum = frame.groupby(stratum_col).size() / periods
    out = pd.DataFrame({"sales_per_period": per_stratum})
    out.index = out.index.astype(str)
    if stock is not None:
        s = pd.Series({str(k): float(v) for k, v in stock.items()})
        out["stock"] = s
        out["share_of_stock_sold_per_period_pct"] = out["sales_per_period"] / s * 100.0
    total = len(frame)
    pairs = repeat_sales_pairs(frame)
    usable = {
        "stratified median / mix-adjusted mean": total,
        "repeat sales (second sales of pairs)": len(pairs),
        "SPAR (with an appraisal)": int(frame[appraisal_col].notna().sum())
        if appraisal_col in frame.columns else 0,
        "hedonic (every characteristic present)": int(frame[list(characteristics)].notna()
                                                      .all(axis=1).sum())
        if set(characteristics) <= set(frame.columns) else 0,
    }
    methods = pd.DataFrame({"sales_used": pd.Series(usable),
                            "share_of_sales_pct": pd.Series(usable) / total * 100.0})
    return pd.concat({"stratum": out, "method": methods})


def suppress_strata(result: PropertyIndexResult, *, min_count: int | None = None) -> pd.DataFrame:
    """The stratified median's per-stratum indices under the platform's
    disclosure control: a stratum-period with fewer than `min_count` sales
    is suppressed (primary), and because the all-strata index for the period
    is published, the smallest remaining stratum in any period with one
    primary suppression is suppressed too (secondary), so the suppressed
    value cannot be backed out. `core.security.suppress_with_secondary`
    does both, as it does for every other published table."""
    from ..core.config import get_settings
    from ..core.security import suppress_with_secondary

    if result.method != "stratified_median" or result.detail is None:
        raise PropertyError("suppression applies to the stratified median's per-stratum table")
    detail = result.detail
    index = detail["index"].stack().rename("index")
    counts = detail["transactions"].stack().rename("transactions")
    long = pd.concat([index, counts], axis=1).reset_index()
    long.columns = ["period", "stratum", "index", "transactions"]
    threshold = min_count if min_count is not None else get_settings().suppression_min_count
    protected = suppress_with_secondary(long, group_col="period", value_col="index",
                                        count_col="transactions", min_count=threshold)
    protected["published"] = [
        "suppressed" if s else f"{v:.4f}"
        for s, v in zip(protected["suppressed"], protected["index"], strict=True)]
    protected["rule"] = np.where(
        protected["secondary_suppressed"],
        "secondary: smallest remaining stratum in a period with one primary suppression",
        np.where(protected["suppressed"], f"primary: fewer than {threshold} sales", ""))
    return protected


# ---------------------------------------------------------------------
# A market with a known answer
# ---------------------------------------------------------------------
def synthetic_market(*, n_properties: int = 600, periods: int = 12, seed: int = 7,
                     growth: Mapping[str, float] | None = None,
                     mix_shift: float = 0.04, sale_rate: float = 0.12) -> pd.DataFrame:
    """Quarterly transactions from a market whose quality-constant price
    index is known, for tests and demonstrations.

    Each property has a floor area and a stratum; its price is
    exp(a + 0.9 ln(area) + stratum effect) x the stratum's true index x
    noise. The true index grows by `growth` per quarter per stratum. Larger
    properties become steadily more likely to sell (`mix_shift` per quarter
    on the log odds per log-area unit), so the quality mix of what sells
    rises -- which a median and a mean carry as price and a hedonic or
    repeat sales index does not. Every property has an appraisal: its true
    value in the first quarter, with 3% noise.
    """
    rng = np.random.default_rng(seed)
    growth = dict(growth or {"North": 0.010, "South": 0.020})
    strata = list(growth)
    stratum = rng.choice(strata, n_properties)
    area = np.exp(rng.normal(np.log(85), 0.35, n_properties))
    effect = {s: 0.25 * i for i, s in enumerate(strata)}
    quality = np.exp(7.5 + 0.9 * np.log(area) + np.array([effect[s] for s in stratum]))
    appraisal = quality * np.exp(rng.normal(0, 0.03, n_properties))
    dates = pd.date_range("2022-01-01", periods=periods, freq="QS")
    rows = []
    for t, period in enumerate(dates):
        tilt = mix_shift * t * (np.log(area) - np.log(85))
        probability = np.clip(sale_rate * np.exp(tilt), 0, 0.9)
        sold = rng.random(n_properties) < probability
        for i in np.flatnonzero(sold):
            true_level = (1 + growth[stratum[i]]) ** t
            rows.append({"property_id": f"P{i:04d}", "period": period, "stratum": stratum[i],
                         "floor_area": round(float(area[i]), 1),
                         "price": round(float(quality[i] * true_level
                                              * np.exp(rng.normal(0, 0.05))), 2),
                         "appraisal": round(float(appraisal[i]), 2)})
    return pd.DataFrame(rows)


def true_index(*, periods: int = 12, growth: Mapping[str, float] | None = None,
               weights: Mapping[str, float] | None = None) -> pd.Series:
    """The known quality-constant index `synthetic_market` was built from,
    as a fixed-weight arithmetic mean of the strata's true indices."""
    growth = dict(growth or {"North": 0.010, "South": 0.020})
    w = pd.Series(dict(weights or dict.fromkeys(growth, 1.0)), dtype=float)
    dates = pd.date_range("2022-01-01", periods=periods, freq="QS")
    levels = pd.DataFrame({s: [(1 + g) ** t * 100 for t in range(periods)]
                           for s, g in growth.items()}, index=dates)
    return (levels * w).sum(axis=1) / w.sum()

