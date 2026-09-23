"""Revision analysis: how much a published figure moved after it was
published, and whether it moved in one direction.

A revision is not an error. A first estimate is made on the data that had
arrived by the deadline; later estimates are made on more of it; the
difference between them is the price of publishing early, and it is a
measurable quantity with a shape. What a reader is entitled to know is how
big that price has been historically, and whether it has a sign -- because a
first estimate that is revised up two years out of three is not early, it is
biased, and the correction is to the process rather than to any one figure.

Everything here is computed from the registry's own vintages. There is no
parallel store of past publications, and there deliberately is not one: a
vintage *is* a registered run, with its input parquet, its configuration,
its code version and its approval, and it reproduces byte for byte
(`core.registry.reproduce`). A separate table of "what we published last
time" would be a second version of the truth, unreproducible, and the first
thing to drift.

The quantities are the standard ones from the revisions literature:

triangle     one row per reference period, one column per vintage: what each
             vintage said about each period. Its lower triangle is empty
             because a vintage cannot speak about a period it predates.
revisions    the successive differences along each row -- what each new
             vintage changed about a period already published.
MR           mean revision, signed. The bias if there is one.
MAR          mean absolute revision. The size of the typical change,
             whichever way it went. MAR large with MR near zero is noise;
             the two close together is a systematic direction.
bias test    a t-test of the mean revision against zero. Reported with its
             sample size, because a "no significant bias" verdict from nine
             revisions is a statement about the sample, not the process.

Sources: OECD/Eurostat guidelines on revisions policy and analysis; the
standard MR/MAR/bias framework as set out in the *OECD Handbook on Data
Revisions* and used by every office that publishes a revisions triangle.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..core.config import RevisionConfig


class RevisionError(ValueError):
    """Raised when the vintages cannot support the analysis asked for."""


@dataclass(frozen=True)
class Vintage:
    """One registered vintage and the series it published."""

    run_id: str
    vintage: int
    created_at: str
    series: pd.Series
    approved: bool = False
    correction_reason: str | None = None
    supersedes_run_id: str | None = None
    label: str = ""

    @property
    def name(self) -> str:
        return f"v{self.vintage}"


def vintages_from_registry(session: Any, run_id: str, cfg: RevisionConfig | None = None, *,
                           series: str | None = None, actor: str = "system") -> list[Vintage]:
    """Reproduce each vintage of a run and take the published series from it.

    Each vintage is re-executed from its own stored input and configuration,
    which is the only way to be sure the series being compared is the one
    that vintage actually published rather than today's code applied to an
    old file. It is also expensive -- one whole pipeline per vintage -- and
    `max_vintages` is the cost control, applied to the most recent ones,
    because a revision analysis is about the recent past.

    `series` names the column; the run's own headline series is used when it
    is not given, so the comparison follows whatever each vintage led with.
    """
    from ..core.registry import reproduce, vintage_chain

    cfg = cfg or RevisionConfig()
    chain = vintage_chain(session, run_id)
    if len(chain) < 2:
        raise RevisionError(
            f"run {run_id!r} has one vintage, so there is nothing to compare it with. A "
            "revision analysis needs a correction: register one, approve it, and correct it "
            "with a stated reason, which produces a second vintage.")
    chain = chain[-cfg.max_vintages:]

    out: list[Vintage] = []
    for run in chain:
        result = reproduce(session, run.run_id, actor=actor)
        indices = result.get("indices")
        if indices is None or not len(indices.columns):
            continue
        column = series or (run.headline_series if run.headline_series in indices.columns
                            else ("All items" if "All items" in indices.columns
                                  else str(indices.columns[0])))
        if column not in indices.columns:
            continue
        out.append(Vintage(
            run_id=run.run_id, vintage=int(run.vintage), created_at=str(run.created_at),
            series=indices[column].rename(f"v{run.vintage}"), approved=bool(run.approved),
            correction_reason=run.correction_reason,
            supersedes_run_id=run.supersedes_run_id, label=str(run.label)))
    if len(out) < 2:
        raise RevisionError(
            "fewer than two vintages produced a comparable series, so there is no revision "
            "to measure")
    return out


def revision_triangle(vintages: Sequence[Vintage]) -> pd.DataFrame:
    """What each vintage said about each reference period.

    Rows are reference periods, columns are vintages in publication order.
    A cell is empty where that vintage had nothing to say -- which is not a
    missing value to be filled but the ordinary shape of the thing: the
    first vintage could not have an estimate for a month that had not
    happened when it was compiled.
    """
    if len(vintages) < 2:
        raise RevisionError("a revision triangle needs at least two vintages")
    frame = pd.DataFrame({v.name: v.series for v in
                          sorted(vintages, key=lambda v: v.vintage)})
    return frame.sort_index()


def revisions(triangle: pd.DataFrame) -> pd.DataFrame:
    """Successive differences along each row: what each vintage changed.

    In index points, not percent, because that is the unit the level is
    published in and the unit a correction is argued about in. A cell is
    empty where either of the two vintages it spans said nothing, so a
    period that first appears in vintage 3 contributes no revision for the
    step into vintage 3 -- appearing is not being revised.
    """
    if triangle.shape[1] < 2:
        raise RevisionError("revisions need at least two vintages to difference")
    out = triangle.diff(axis=1).iloc[:, 1:]
    out.columns = [f"{a}->{b}" for a, b in zip(triangle.columns[:-1], triangle.columns[1:],
                                               strict=True)]
    return out


@dataclass(frozen=True)
class BiasTest:
    """A t-test of the mean revision against zero."""

    mean: float
    standard_error: float
    t_statistic: float
    p_value: float
    n: int
    alpha: float
    significant: bool

    @property
    def verdict(self) -> str:
        if self.n < 3:
            return (f"{self.n} revision(s) is too few to test for bias; this is a statement "
                    "about the sample, not about the process")
        direction = "upward" if self.mean > 0 else "downward"
        if self.significant:
            return (f"the mean revision of {self.mean:+.3f} index points is significantly "
                    f"different from zero (p = {self.p_value:.3f}, n = {self.n}): these "
                    f"estimates have been revised {direction} systematically, which is a "
                    "property of the process rather than of any one figure")
        return (f"the mean revision of {self.mean:+.3f} index points is not significantly "
                f"different from zero (p = {self.p_value:.3f}, n = {self.n}): no evidence of "
                "systematic bias in this sample")


def bias_test(values: pd.Series | np.ndarray, alpha: float = 0.05) -> BiasTest:
    """Is the mean revision different from zero?

    A one-sample t-test, two-sided. The null is that revisions average out --
    that publishing early costs precision but not accuracy. Rejecting it
    says the early estimate is systematically on one side, which is a
    fixable property of the compilation rather than bad luck.

    Reported with `n` attached and a verdict that says so, because the
    failure mode of this test is not a wrong p-value, it is a reader taking
    "not significant" from nine observations as evidence of no bias.
    """
    from scipy import stats

    array = np.asarray(pd.Series(values).dropna(), dtype=float)
    array = array[np.isfinite(array)]
    n = int(array.size)
    if n < 2:
        return BiasTest(mean=float(array.mean()) if n else float("nan"),
                        standard_error=float("nan"), t_statistic=float("nan"),
                        p_value=float("nan"), n=n, alpha=alpha, significant=False)
    mean = float(array.mean())
    standard_error = float(array.std(ddof=1) / np.sqrt(n))
    if standard_error == 0:
        return BiasTest(mean=mean, standard_error=0.0,
                        t_statistic=float("inf") if mean else 0.0,
                        p_value=0.0 if mean else 1.0, n=n, alpha=alpha,
                        significant=bool(mean))
    t_statistic, p_value = stats.ttest_1samp(array, 0.0)
    return BiasTest(mean=mean, standard_error=standard_error,
                    t_statistic=float(t_statistic), p_value=float(p_value), n=n,
                    alpha=alpha, significant=bool(p_value < alpha))


def published_vs_current(triangle: pd.DataFrame, period: Any = None) -> pd.DataFrame:
    """For each reference period, what was first published and what stands now.

    `period` narrows it to one row. The columns are the first vintage that
    had a figure for the period, the latest that has one, the two values,
    and the difference -- which is the question anyone asking about
    revisions is actually asking: has this month's number changed since I
    used it, and by how much.
    """
    if triangle.empty:
        raise RevisionError("an empty triangle has nothing to compare")
    rows: list[dict[str, Any]] = []
    for reference, values in triangle.iterrows():
        present = values.dropna()
        if present.empty:
            continue
        first_name, last_name = str(present.index[0]), str(present.index[-1])
        first, last = float(present.iloc[0]), float(present.iloc[-1])
        rows.append({
            "period": reference, "first_vintage": first_name, "first_published": first,
            "current_vintage": last_name, "current": last, "revision_pp": last - first,
            "revision_pct": (last - first) / first * 100.0 if first else float("nan"),
            "vintages": int(present.size), "revised": bool(abs(last - first) > 1e-12)})
    out = pd.DataFrame(rows).set_index("period").sort_index()
    if period is not None:
        stamp = pd.Timestamp(period)
        if stamp not in out.index:
            raise RevisionError(
                f"{stamp:%Y-%m-%d} is not a reference period any vintage published")
        return out.loc[[stamp]]
    return out


@dataclass(frozen=True)
class RevisionAnalysis:
    """Everything the revision stage produced."""

    triangle: pd.DataFrame
    revisions: pd.DataFrame
    comparison: pd.DataFrame
    mean_revision: float
    mean_absolute_revision: float
    bias: BiasTest
    vintages: tuple[Vintage, ...]
    series_name: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def n_revisions(self) -> int:
        return int(np.isfinite(self.revisions.to_numpy(dtype=float)).sum())

    @property
    def revised_periods(self) -> int:
        return int(self.comparison["revised"].sum()) if len(self.comparison) else 0


def analyse(vintages: Sequence[Vintage], cfg: RevisionConfig | None = None, *,
            series_name: str = "") -> RevisionAnalysis:
    """The whole revision picture for one series across its vintages."""
    cfg = cfg or RevisionConfig()
    triangle = revision_triangle(vintages)
    changes = revisions(triangle)
    flat = pd.Series(changes.to_numpy(dtype=float).ravel()).dropna()
    notes: list[str] = []
    if flat.empty:
        notes.append(
            "no reference period is published by two vintages, so nothing has been revised: "
            "the vintages extend the series rather than correcting it")
    return RevisionAnalysis(
        triangle=triangle, revisions=changes,
        comparison=published_vs_current(triangle),
        mean_revision=float(flat.mean()) if len(flat) else float("nan"),
        mean_absolute_revision=float(flat.abs().mean()) if len(flat) else float("nan"),
        bias=bias_test(flat, cfg.bias_alpha), vintages=tuple(vintages),
        series_name=series_name, notes=tuple(notes))


def revision_note(analysis: RevisionAnalysis | None) -> str:
    """One paragraph for the method note. Empty when there is no revision
    history, so a caller can concatenate it unconditionally."""
    if analysis is None:
        return ""
    return (
        f"This figure is vintage {analysis.vintages[-1].vintage} of "
        f"{len(analysis.vintages)} held in the registry. Across "
        f"{analysis.n_revisions} revision(s) to {analysis.revised_periods} reference "
        f"period(s), the mean revision was {analysis.mean_revision:+.3f} index points and the "
        f"mean absolute revision {analysis.mean_absolute_revision:.3f}. Testing the mean "
        f"against zero, {analysis.bias.verdict}. Every earlier vintage remains registered and "
        "reproducible; a correction adds a vintage and never alters the one it supersedes.")
