"""Chain linking, rebasing, link factors, price updating, and the chain
drift diagnostic that says when chaining has stopped measuring price
change and started accumulating error.

The operations here are all rescalings, and the distinction that matters
is which of them can change a movement and which cannot:

rebase          divides the whole series by its level at one period.
                Changes every level, changes no ratio between two periods.
                Purely presentational.
link            multiplies one series onto the end of another at an
                overlap period. Changes levels, preserves each series'
                own internal movements, and takes on trust that the two
                series are measuring the same thing at the join.
chain           multiplies period-on-period links into a level. Changes
                what is measured: a chained index and a direct index over
                the same span are different numbers, and the gap between
                them is chain drift.

Chain drift is the one that bites. A chained index is transitive only if
its elementary formula is: Jevons and Dutot chain to exactly their direct
counterparts, Carli does not (CPI Manual 2020, paragraph 8.18, and Table
8.3's worked example where a chained Carli invents 6.7 percent inflation
from prices that ended where they began). Weighted chained indices can
drift badly too, in either direction, when quantities lag price changes --
the manual's Table 10.1 shows a chained Tornqvist ending 22 percent below
a direct one on data where nothing net changed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..core.config import IndexConfig
from .index import resolve_index_reference_period

#: Default threshold, in percentage points of index level, above which a
#: chained-versus-direct gap is worth a human look. Deliberately a
#: parameter everywhere rather than a constant read from here: what counts
#: as material drift depends on the series' own volatility and on what the
#: index is used for, and a threshold that cannot be argued with is a
#: threshold nobody has thought about.
DEFAULT_DRIFT_THRESHOLD_PP = 1.0


def rebase(series: pd.Series, reference_period: pd.Timestamp, base_value: float = 100.0
           ) -> pd.Series:
    """Rescale so the series reads `base_value` at `reference_period`.

    Multiplying every level by one constant cancels out of any ratio
    between two periods, so no period-on-period movement, and no growth
    rate, is affected by this. That is what makes rebasing safe to do for
    presentation and unsafe to confuse with recomputation.
    """
    if reference_period not in series.index:
        raise ValueError(
            f"cannot rebase to {reference_period:%Y-%m-%d}: it is not in the series "
            f"(range {series.index[0]:%Y-%m-%d} to {series.index[-1]:%Y-%m-%d}). Rebasing to "
            "an absent period would need an interpolated divisor, which would be a "
            "fabricated number sitting under every published level.")
    divisor = float(series.loc[reference_period])
    if not np.isfinite(divisor) or divisor == 0:
        raise ValueError(
            f"the series' level at {reference_period:%Y-%m-%d} is {divisor}, which cannot "
            "be a rebasing divisor")
    return series / divisor * base_value


def rebase_to_config(series: pd.Series, cfg: IndexConfig) -> pd.Series:
    """Rebase using the same resolution rule `engine.index.build_index`
    applies, so a series rebased here and one rebased there agree."""
    return rebase(series, resolve_index_reference_period(cfg, series.index), cfg.base_value)


def link_factor(old: pd.Series, new: pd.Series, overlap: pd.Timestamp) -> float:
    """The constant that puts `new` onto `old`'s level at the overlap
    period.

        f = old(overlap) / new(overlap)

    The number an office needs to publish when it introduces a new basket:
    everything about the new series is preserved except its level, which
    is set so that nothing jumps at the join.
    """
    for name, series in (("old", old), ("new", new)):
        if overlap not in series.index:
            raise ValueError(
                f"overlap period {overlap:%Y-%m-%d} is not present in the {name} series; "
                "a link factor needs both series to have a value at the same period, which "
                "is the entire content of the claim that they can be joined")
    numerator, denominator = float(old.loc[overlap]), float(new.loc[overlap])
    if not np.isfinite(denominator) or denominator == 0:
        raise ValueError(f"the new series' level at the overlap is {denominator}")
    return numerator / denominator


def splice(old: pd.Series, new: pd.Series, overlap: pd.Timestamp) -> pd.Series:
    """Join a new series onto an old one at an overlap period, scaling the
    new one to match.

    The old series is kept up to the overlap and the rescaled new series
    from the overlap on, so the overlap period itself has one value, not
    two. What this cannot do is make the join meaningful: it assumes the
    two series measure the same thing at the overlap, and if the basket
    changed underneath, that assumption is where the error lives -- not in
    the arithmetic, which is exact.
    """
    factor = link_factor(old, new, overlap)
    scaled = new * factor
    kept = old.loc[old.index < overlap]
    joined: pd.Series = pd.concat([kept, scaled.loc[scaled.index >= overlap]])
    return joined


def chain(links: pd.Series, base_value: float = 100.0) -> pd.Series:
    """Compound period-on-period links into a level series.

    `links` are ratios (1.02 for a 2 percent rise), indexed by the period
    each link *arrives at*; the first period's link is ignored, as it has
    no predecessor. A non-finite link holds the level rather than
    destroying the rest of the series, matching `build_index`'s behaviour
    when too few items matched to compute a comparison.
    """
    values, level = [], base_value
    for i, link in enumerate(links.to_numpy(dtype=float)):
        if i > 0 and np.isfinite(link):
            level = level * link
        values.append(level)
    return pd.Series(values, index=links.index)


def price_update(series: pd.Series, from_period: pd.Timestamp, to_period: pd.Timestamp) -> float:
    """The price change between two periods of a finished index, as a
    ratio. The factor that carries a weight from the weight reference
    period to the price reference period; see
    `engine.bilateral.price_update_shares` for the per-item version."""
    for name, period in (("from_period", from_period), ("to_period", to_period)):
        if period not in series.index:
            raise ValueError(f"{name} {period:%Y-%m-%d} is not present in the series")
    start = float(series.loc[from_period])
    if not np.isfinite(start) or start == 0:
        raise ValueError(f"the series' level at {from_period:%Y-%m-%d} is {start}")
    return float(series.loc[to_period]) / start


@dataclass
class ChainDriftReport:
    """Chained against direct, over the same span, for one series."""

    chained_level: float
    direct_level: float
    drift_pp: float
    """chained - direct, in percentage points of index level, at the end
    of the span."""
    drift_pct: float
    """The same gap relative to the direct level, which is the number that
    is comparable across series sitting at different levels."""
    threshold_pp: float
    exceeds_threshold: bool
    span_start: pd.Timestamp
    span_end: pd.Timestamp

    @property
    def message(self) -> str:
        direction = "above" if self.drift_pp > 0 else "below"
        return (
            f"chained index ends {abs(self.drift_pp):.2f} points {direction} the direct "
            f"index over {self.span_start:%Y-%m} to {self.span_end:%Y-%m} "
            f"({self.drift_pct:+.2f}% of the direct level, threshold "
            f"{self.threshold_pp:.2f} points)")


def chain_drift(
    chained: pd.Series,
    direct: pd.Series,
    threshold_pp: float = DEFAULT_DRIFT_THRESHOLD_PP,
) -> ChainDriftReport:
    """Compare a chained series to a direct one over their common span.

    Both are rebased to their first common period before comparison,
    because otherwise this measures whatever different reference periods
    the two were built under rather than drift. A transitive formula
    (Jevons, Dutot) makes this exactly zero; anything else is telling you
    how much of the chained series' final level was manufactured by the
    chaining rather than observed in the prices.
    """
    common = chained.index.intersection(direct.index)
    if len(common) < 2:
        raise ValueError(
            "chain drift needs at least two periods common to both series; with fewer there "
            "is no span over which chaining could have drifted")
    start, end = common[0], common[-1]
    c = rebase(chained.loc[common], start)
    d = rebase(direct.loc[common], start)

    chained_level, direct_level = float(c.loc[end]), float(d.loc[end])
    drift_pp = chained_level - direct_level
    drift_pct = drift_pp / direct_level * 100.0 if direct_level else float("nan")
    return ChainDriftReport(
        chained_level=chained_level, direct_level=direct_level,
        drift_pp=drift_pp, drift_pct=drift_pct,
        threshold_pp=threshold_pp, exceeds_threshold=abs(drift_pp) > threshold_pp,
        span_start=start, span_end=end)


def chain_drift_table(
    chained: pd.DataFrame,
    direct: pd.DataFrame,
    threshold_pp: float = DEFAULT_DRIFT_THRESHOLD_PP,
) -> pd.DataFrame:
    """`chain_drift` per column, as a table, flagged against the
    threshold. Only columns present in both frames are compared."""
    rows = {}
    for column in [c for c in chained.columns if c in direct.columns]:
        try:
            report = chain_drift(chained[column], direct[column], threshold_pp)
        except ValueError:
            continue
        rows[column] = {
            "chained": report.chained_level, "direct": report.direct_level,
            "drift_pp": report.drift_pp, "drift_pct": report.drift_pct,
            "exceeds_threshold": report.exceeds_threshold}
    if not rows:
        return pd.DataFrame(
            columns=["chained", "direct", "drift_pp", "drift_pct", "exceeds_threshold"])
    return pd.DataFrame(rows).T.sort_values("drift_pp", key=abs, ascending=False)
