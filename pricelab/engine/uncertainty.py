"""Sampling uncertainty: design-based bootstrap confidence intervals for
index movements, or a refusal with the reason.

The design matters more than the arithmetic. Price quotes are not a simple
random sample: outlets are sampled, often within strata (regions, outlet
types), and every quote from one outlet shares that outlet's pricing. Resampling
individual quotes treats them as independent, and on clustered data it
reports an interval narrower than the truth -- which is worse than reporting
none, because it is believed. So:

* An interval is computed only for a *declared* design: which column names
  the strata, and which names the clusters (the primary sampling units, the
  outlets). A collection uploaded with no design information gets no
  interval, and `DesignUnknown` says why. Simple random sampling of quotes
  is never assumed.
* The resampling is at the level the sample was drawn: within each stratum,
  clusters are drawn with replacement, whole, with every quote they hold.
  Following Rao and Wu (1988), n_h - 1 clusters are drawn from a stratum
  of n_h, which makes the bootstrap variance of a mean match the
  with-replacement design variance without reweighting. No finite
  population correction is applied, which errs towards a wider interval
  when a large share of a stratum's outlets is sampled; that is stated.
* The statistic is the direct matched-model movement of the headline
  between two periods: in each category the geometric mean of the price
  relatives of items priced in both, the categories combined as the run
  combines them (weighted arithmetic with expenditure weights, equally
  weighted geometric without). A chained index's movement over the same
  span differs from it; the interval is for this statistic, and says so.

An interval measures one thing: how much the movement would differ had a
different sample been drawn from the same population by the same design. It
says nothing about the choices of method -- formula, aggregation, imputation
-- which `engine/sensitivity.py` measures, separately, and which must never
be added to it or drawn on the same axis (see `reporting/charts.check_axes`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

__all__ = [
    "DesignUnknown",
    "IntervalResult",
    "SamplingDesign",
    "UncertaintyError",
    "bootstrap_interval",
    "compare_with_naive",
    "matched_relatives",
    "NOT_QUANTIFIED",
]

#: What an output says in place of an interval it does not have.
NOT_QUANTIFIED = ("Sampling uncertainty has not been quantified for this figure: no sampling "
                  "design has been declared for the collection, and an interval that assumed "
                  "simple random sampling of price quotes would understate it.")


class UncertaintyError(ValueError):
    """The data or the design cannot support an interval."""


class DesignUnknown(UncertaintyError):
    """No sampling design was declared, so no interval is published."""


@dataclass(frozen=True)
class SamplingDesign:
    """How the sample was drawn, as declared by someone who knows."""

    cluster: str
    """The column naming the primary sampling unit: the outlet, usually."""
    strata: str | None = None
    """The column naming the strata the clusters were sampled within, or
    None for a single stratum."""
    declared_by: str = ""
    """Who declared the design, and where: an interval rests on it."""

    @property
    def statement(self) -> str:
        where = (f"clusters ({self.cluster}) sampled within strata ({self.strata})"
                 if self.strata else f"clusters ({self.cluster}) sampled from one stratum")
        return (f"design assumed: {where}, as declared{f' by {self.declared_by}' if self.declared_by else ''}; "
                "resampled by the Rao-Wu bootstrap, whole clusters within strata, with no finite "
                "population correction")


@dataclass(frozen=True)
class IntervalResult:
    estimate_pct: float
    lower_pct: float
    upper_pct: float
    level: float
    start: pd.Timestamp | None
    end: pd.Timestamp | None
    replicates: np.ndarray
    design: SamplingDesign | None
    naive: bool
    clusters_per_stratum: pd.Series
    notes: tuple[str, ...] = field(default_factory=tuple)

    kind = "sampling uncertainty"

    @property
    def width_pp(self) -> float:
        return self.upper_pct - self.lower_pct

    @property
    def label(self) -> str:
        """What the interval is, what it rests on, and what it is not."""
        if self.naive:
            basis = ("NAIVE: resampling individual quotes as if they were a simple random sample. "
                     "Shown only for comparison and never published: on clustered data it is "
                     "too narrow")
        else:
            assert self.design is not None
            basis = self.design.statement
        span = (f"from {self.start:%b %Y} to {self.end:%b %Y}"
                if self.start is not None and self.end is not None else "between the periods")
        return (f"{self.level:.0%} confidence interval, sampling uncertainty only, for the change "
                f"in All items {span}: "
                f"{self.lower_pct:+.2f}% to {self.upper_pct:+.2f}% around {self.estimate_pct:+.2f}% "
                f"({len(self.replicates):,} bootstrap replicates; {basis}). It measures how much "
                "the change would move had a different sample been drawn, not how much it would "
                "move under a different method -- that is the sensitivity range, reported "
                "separately and never combined with this.")


# ---------------------------------------------------------------------
# The statistic
# ---------------------------------------------------------------------
def matched_relatives(panel: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, *,
                      group: str = "category", price_col: str = "price_imputed",
                      extra: tuple[str, ...] = ()) -> pd.DataFrame:
    """One row per item priced in both periods: its category, its log price
    relative, and the columns in `extra` (the design's cluster and strata)."""
    frame = panel[panel["period"].isin([start, end])].dropna(subset=[price_col])
    frame = frame[frame[price_col] > 0]
    keys = ["item_id", group, *[c for c in extra if c not in ("item_id", group)]]
    wide = frame.pivot_table(index=keys, columns="period", values=price_col, aggfunc="mean")
    if start not in wide.columns or end not in wide.columns:
        raise UncertaintyError(f"no prices in {start:%Y-%m} or {end:%Y-%m}")
    both = wide[[start, end]].dropna()
    if both.empty:
        raise UncertaintyError(f"no item is priced in both {start:%Y-%m} and {end:%Y-%m}")
    out = both.reset_index()[keys]
    out["log_relative"] = np.log(both[end].to_numpy() / both[start].to_numpy())
    return out.rename(columns={group: "category"})


def _headline(means: np.ndarray, weights: np.ndarray | None) -> np.ndarray:
    """Category mean log relatives (replicates x categories) to the headline
    movement in percent, aggregated as the run aggregates."""
    if weights is None:
        return np.asarray((np.exp(np.nanmean(means, axis=1)) - 1.0) * 100.0, dtype=float)
    w = np.where(np.isnan(means), 0.0, weights)
    levels = np.where(np.isnan(means), 0.0, np.exp(means))
    return np.asarray(((levels * w).sum(axis=1) / w.sum(axis=1) - 1.0) * 100.0, dtype=float)


def bootstrap_interval(relatives: pd.DataFrame, design: SamplingDesign | None, *,
                       replicates: int = 999, level: float = 0.95, seed: int = 0,
                       weights: Mapping[str, float] | None = None, naive: bool = False,
                       start: pd.Timestamp | None = None, end: pd.Timestamp | None = None
                       ) -> IntervalResult:
    """Percentile bootstrap interval for the headline movement.

    `relatives` is `matched_relatives`' output, carrying the design's cluster
    and strata columns. With `design=None` and `naive=False` this refuses:
    `DesignUnknown`. `naive=True` resamples individual quotes, ignoring the
    design, and exists only to show how much narrower that is.
    """
    if not naive and design is None:
        raise DesignUnknown(NOT_QUANTIFIED)
    if not 0.5 < level < 1.0:
        raise UncertaintyError("the confidence level must be between 0.5 and 1")
    categories = sorted(relatives["category"].astype(str).unique())
    cat_index = {c: i for i, c in enumerate(categories)}
    w = (np.array([float(weights.get(c, np.nan)) for c in categories])
         if weights is not None else None)
    if w is not None and not np.isfinite(w).all():
        missing = [c for c, v in zip(categories, w, strict=True) if not np.isfinite(v)]
        raise UncertaintyError(f"no expenditure weight for {missing}")
    rng = np.random.default_rng(seed)
    col = relatives["category"].astype(str).map(cat_index).to_numpy()
    values = relatives["log_relative"].to_numpy(dtype=float)
    notes: list[str] = []

    if naive:
        n = len(values)
        units = np.arange(n)
        sums = np.zeros((n, len(categories)))
        sums[units, col] = values
        counts = np.zeros((n, len(categories)))
        counts[units, col] = 1.0
        draws = rng.multinomial(n, np.full(n, 1.0 / n), size=replicates).astype(float)
        per_stratum = pd.Series({"all quotes": n})
    else:
        assert design is not None
        for column in (design.cluster, design.strata):
            if column is not None and column not in relatives.columns:
                raise UncertaintyError(f"the declared design names {column!r}, which the data "
                                       "does not have")
        strata = (relatives[design.strata].astype(str) if design.strata
                  else pd.Series("all", index=relatives.index))
        clusters = strata + "\x1f" + relatives[design.cluster].astype(str)
        cluster_codes, cluster_names = pd.factorize(clusters)
        k = len(cluster_names)
        sums = np.zeros((k, len(categories)))
        counts = np.zeros((k, len(categories)))
        np.add.at(sums, (cluster_codes, col), values)
        np.add.at(counts, (cluster_codes, col), 1.0)
        stratum_of = pd.Series(cluster_names).str.split("\x1f").str[0].to_numpy()
        per_stratum = pd.Series(stratum_of).value_counts().sort_index()
        thin = per_stratum[per_stratum < 2]
        if len(thin):
            raise UncertaintyError(
                f"stratum(s) {list(thin.index)} hold a single cluster: there is no variation "
                "within them to resample. Collapse each with a similar stratum and declare that, "
                "rather than have its sampling variance silently set to zero")
        draws = np.zeros((replicates, k))
        for stratum in per_stratum.index:
            members = np.flatnonzero(stratum_of == stratum)
            m = len(members) - 1
            picks = rng.integers(0, len(members), size=(replicates, m))
            np.add.at(draws, (np.repeat(np.arange(replicates), m), members[picks].ravel()), 1.0)
        notes.append(f"{k:,} clusters in {len(per_stratum)} stratum(s); n_h - 1 drawn per "
                     "stratum per replicate")

    with np.errstate(invalid="ignore", divide="ignore"):
        means = (draws @ sums) / (draws @ counts)
        point = (sums.sum(axis=0) / counts.sum(axis=0))[None, :]
    headline = _headline(means, w)
    estimate = float(_headline(point, w)[0])
    alpha = (1.0 - level) / 2.0
    lower, upper = np.nanquantile(headline, [alpha, 1.0 - alpha])
    return IntervalResult(
        estimate_pct=estimate, lower_pct=float(lower), upper_pct=float(upper), level=level,
        start=pd.Timestamp(start) if start is not None else None,
        end=pd.Timestamp(end) if end is not None else None,
        replicates=headline, design=design, naive=naive,
        clusters_per_stratum=per_stratum.astype(int), notes=tuple(notes))


@dataclass(frozen=True)
class DesignComparison:
    design_based: IntervalResult
    naive: IntervalResult

    @property
    def width_ratio(self) -> float:
        return self.design_based.width_pp / self.naive.width_pp

    @property
    def statement(self) -> str:
        return (f"Resampling whole clusters within strata gives an interval "
                f"{self.design_based.width_pp:.2f} points wide; resampling individual quotes, as "
                f"if they were independent, gives {self.naive.width_pp:.2f} -- "
                f"{self.width_ratio:.2f} times narrower than the design supports. The naive "
                "interval is shown for this comparison only and is not published.")


def compare_with_naive(relatives: pd.DataFrame, design: SamplingDesign, **kwargs: object
                       ) -> DesignComparison:
    """The design-based interval beside the naive one, on the same data and
    the same random stream, so the difference is the design and nothing else."""
    return DesignComparison(
        design_based=bootstrap_interval(relatives, design, **kwargs),  # type: ignore[arg-type]
        naive=bootstrap_interval(relatives, None, naive=True, **kwargs))  # type: ignore[arg-type]
