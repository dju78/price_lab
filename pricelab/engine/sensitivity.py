"""Methodological sensitivity: the headline recomputed under each defensible
alternative choice, and the spread reported as a range with the settings at
each end named.

Sensitivity is not uncertainty. A confidence interval
(`engine/uncertainty.py`) answers how much the number would move had a
different sample been drawn from the same population. The sensitivity range
answers how much it would move had a different defensible choice been made
on the same data -- a formula, an aggregation, a multilateral method, a
quality adjustment treatment, an imputation method, a seasonal treatment.
They are different questions with different answers, and a reader who adds
them, or reads one as the other, will understate one and overstate the
other. So the range carries its own label (`SensitivityResult.label`), which
says what it is and what it is not; it is never combined with an interval
into one band; and `reporting/charts.check_axes` refuses any axis that draws
both.

Dimensions whose alternatives the data cannot support are listed with the
reason rather than left out, so the range cannot look narrower than it is
merely because some choices were not tried.

The range is a lower bound. One choice is varied at a time, so what two
choices do together -- a different formula *and* a different imputation --
is never computed, and the true methodological range is wider. The label
says so in those terms, wherever the range appears.

Imputation is the one dimension whose effect depends on how much of the
aggregate it touches, so every row carries the imputed share of the
aggregate under that setting, and the result carries the published run's:
a seventeen-point swing from filling a tenth of the aggregate reads very
differently from the same swing from filling half of it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import Any

import numpy as np
import pandas as pd

__all__ = [
    "DIMENSIONS",
    "LOWER_BOUND",
    "ImputedShare",
    "SensitivityResult",
    "imputed_share",
    "sensitivity",
]

#: The words every statement of the range carries.
LOWER_BOUND = ("It is a lower bound on the methodological range: one choice is varied at a "
               "time, so interactions between choices are excluded and the true "
               "methodological range is wider.")


@dataclass(frozen=True)
class ImputedShare:
    """How much of the aggregate is filled rather than observed.

    Each category's share of its expected quotes that were imputed, weighted
    by the category's weight in the aggregate (equal weights when the
    collection has none): the share of the headline that is model output.
    `final` is the headline period; `run` the mean over every period."""

    final: float
    run: float
    period: pd.Timestamp
    missing_final: float
    """The share of the final period's aggregate neither observed nor imputed."""

    @property
    def statement(self) -> str:
        return (f"{self.final:.1%} of the aggregate is imputed in {self.period:%b %Y} "
                f"({self.run:.1%} averaged over the run); {self.missing_final:.1%} is neither "
                "observed nor imputed there.")


def imputed_share(imputed: pd.DataFrame) -> ImputedShare:
    """The imputed share of the aggregate, final period and whole run."""
    from .imputation import response_rates
    from .index import category_weights

    rates = response_rates(imputed)
    weights = category_weights(imputed)
    categories = rates.index.get_level_values("category").unique()
    w = (pd.Series(weights).reindex(categories).fillna(0.0) if weights is not None
         else pd.Series(1.0, index=categories))
    w = w / w.sum()
    missing = rates["still_missing"] / rates["expected"]

    def weighted(column: pd.Series) -> pd.Series:
        table = column.unstack("category").reindex(columns=categories).fillna(0.0)
        return (table * w).sum(axis=1)

    shares, gaps = weighted(rates["imputed_share"]), weighted(missing)
    final = pd.Timestamp(shares.index.max())
    return ImputedShare(final=float(shares[final]), run=float(shares.mean()), period=final,
                        missing_final=float(gaps[final]))

DIMENSIONS: tuple[str, ...] = ("elementary formula", "aggregation formula",
                               "multilateral method and window", "quality adjustment",
                               "imputation method", "seasonal treatment")


@dataclass(frozen=True)
class SensitivityResult:
    baseline: float
    """The run's own headline: All items in the final period."""
    period: pd.Timestamp
    reference: pd.Timestamp
    table: pd.DataFrame
    """dimension, setting, headline, difference_points, status, reason,
    imputed_share_final, imputed_share_run."""
    low: tuple[str, str, float]
    high: tuple[str, str, float]
    imputed: ImputedShare | None = None
    """The published run's imputed share of the aggregate."""
    notes: tuple[str, ...] = field(default_factory=tuple)

    kind = "methodological sensitivity"

    @property
    def computed(self) -> pd.DataFrame:
        return self.table[self.table["status"] == "computed"]

    @property
    def range_points(self) -> float:
        return self.high[2] - self.low[2]

    @property
    def label(self) -> str:
        """What the range is, and that it is not a confidence interval."""
        n = len(self.computed)
        return (f"Methodological sensitivity range -- not a confidence interval: across {n} "
                f"defensible alternative choices on the same data, All items at "
                f"{self.period:%b %Y} ({self.reference:%b %Y} = 100) runs from "
                f"{self.low[2]:.2f} ({self.low[0]}: {self.low[1]}) to {self.high[2]:.2f} "
                f"({self.high[0]}: {self.high[1]}), a spread of {self.range_points:.2f} points "
                f"around the published {self.baseline:.2f}. {LOWER_BOUND} It measures how "
                "much the number depends on choices of method, not on the sample drawn; "
                "sampling uncertainty is reported separately and must not be added to it.")

    @property
    def imputation_statement(self) -> str:
        """The published run's imputed share beside the most any imputation
        alternative fills -- what a reader needs to judge the imputation
        rows."""
        if self.imputed is None:
            return ""
        rows = self.table[(self.table["dimension"] == "imputation method")
                          & (self.table["status"] == "computed")]
        text = f"Imputed share of the aggregate in the published run: {self.imputed.statement}"
        if len(rows) and rows["imputed_share_final"].notna().any():
            top = rows.sort_values(["imputed_share_final", "imputed_share_run"]).iloc[-1]
            swing = rows["difference_points"].abs().max()
            text += (f" The imputation alternatives move the headline by up to {swing:.2f} "
                     f"points; the most any of them fills is {top['imputed_share_final']:.1%} "
                     f"of the aggregate in the final period ({top['imputed_share_run']:.1%} over "
                     f"the run), under {top['setting']}.")
        return text


def _final(series: pd.Series) -> float:
    clean = series.dropna()
    return float(clean.iloc[-1]) if len(clean) else float("nan")


def sensitivity(df: pd.DataFrame, cfg: Any, *, baseline: Mapping[str, Any] | None = None,
                multilateral_windows: Sequence[int] = (13, 25),
                dimensions: Sequence[str] = DIMENSIONS) -> SensitivityResult:
    """Recompute the headline under every alternative the data supports.

    `df` is the standardised collection and `cfg` the run's `RunConfig`;
    `baseline` the run's result, compiled if not given. Every alternative
    differs from the run in one setting only, so each row says what that
    one choice is worth.
    """
    from .. import run_pipeline
    from ..core.config import SeasonalConfig
    from ..engine.index import formula_availability
    from . import multilateral as ml
    from . import seasonal as sn
    from .aggregation import weighted_aggregate
    from .index import category_weights

    res = dict(baseline) if baseline is not None else run_pipeline(df, cfg)
    if "indices" not in res:
        raise ValueError("the baseline run has no index to be sensitive about")
    head = res["indices"]["All items"]
    base_value = _final(head)
    period = pd.Timestamp(head.dropna().index[-1])
    reference = pd.Timestamp(head.dropna().index[0])
    rows: list[dict[str, Any]] = []
    imputed = res["imputed"]
    published_share = imputed_share(imputed)
    # The imputed share under the setting being tried: a rerun records its
    # own; every other alternative works on the published run's panel.
    last_share: list[ImputedShare] = []

    def add(dimension: str, setting: str, compute: Callable[[], float]) -> None:
        last_share.clear()
        try:
            value = compute()
        except Exception as exc:        # noqa: BLE001 - every failure is reported as a reason
            rows.append({"dimension": dimension, "setting": setting, "headline": np.nan,
                         "difference_points": np.nan, "status": "not applicable",
                         "reason": str(exc).split("\n")[0][:240],
                         "imputed_share_final": np.nan, "imputed_share_run": np.nan})
            return
        share = last_share[0] if last_share else published_share
        rows.append({"dimension": dimension, "setting": setting, "headline": value,
                     "difference_points": value - base_value,
                     "status": "computed" if np.isfinite(value) else "not applicable",
                     "reason": "" if np.isfinite(value) else "produced no final-period value",
                     "imputed_share_final": share.final, "imputed_share_run": share.run})

    def not_applicable(dimension: str, setting: str, reason: str) -> None:
        rows.append({"dimension": dimension, "setting": setting, "headline": np.nan,
                     "difference_points": np.nan, "status": "not applicable", "reason": reason,
                     "imputed_share_final": np.nan, "imputed_share_run": np.nan})

    def rerun(**changes: Any) -> float:
        updated = cfg.model_copy(deep=True)
        for path, value in changes.items():
            section, attribute = path.split("__")
            setattr(getattr(updated, section), attribute, value)
        result = run_pipeline(df, updated)
        last_share.append(imputed_share(result["imputed"]))
        return _final(result["indices"]["All items"])

    categories = [c for c in res["indices"].columns if c != "All items"]
    weights = category_weights(imputed)

    if "elementary formula" in dimensions:
        available = formula_availability(imputed)
        for formula in ("jevons", "dutot", "carli", "laspeyres", "paasche", "fisher",
                        "tornqvist", "walsh", "geometric_laspeyres", "geometric_paasche"):
            if formula == cfg.index.formula:
                continue
            reason = available.get(formula)
            if formula == "laspeyres" and weights is None:
                reason = ("without expenditure weights the Laspeyres falls back to the Jevons, so "
                          "it is not a different choice on this collection")
            if reason:
                not_applicable("elementary formula", formula, reason)
            else:
                add("elementary formula", formula, partial(rerun, index__formula=formula))

    if "aggregation formula" in dimensions:
        levels = res["indices"][categories]
        equal = dict.fromkeys(categories, 1.0)
        add("aggregation formula", "equally weighted arithmetic mean",
            lambda: _final(weighted_aggregate(levels, equal)))
        if weights is not None:     # otherwise it is the run's own aggregation
            add("aggregation formula", "equally weighted geometric mean",
                lambda: _final(np.exp(np.log(levels).mean(axis=1))))
        add("aggregation formula", "equally weighted harmonic mean",
            lambda: _final(len(categories) / (1.0 / levels).sum(axis=1)))
        if weights is not None:
            w = pd.Series(weights).reindex(categories).fillna(0.0)
            add("aggregation formula", "expenditure-weighted geometric mean",
                lambda: _final(np.exp((np.log(levels) * w).sum(axis=1) / w.sum())))
        else:
            not_applicable("aggregation formula", "expenditure-weighted forms",
                           "the collection carries no expenditure weights")

    if "multilateral method and window" in dimensions:
        for method in ml.METHODS:
            needs = ml.METHOD_REQUIREMENTS.get(method, ())
            if "quantities" in needs and "quantity" not in imputed.columns:
                not_applicable("multilateral method and window", ml.METHOD_LABELS[method],
                               "needs quantities or expenditure, which this collection lacks")
                continue
            if "characteristics" in needs:
                not_applicable("multilateral method and window", ml.METHOD_LABELS[method],
                               "needs item characteristics, which this run was not given")
                continue
            for window in multilateral_windows:
                add("multilateral method and window",
                    f"{ml.METHOD_LABELS[method]}, {window}-period window",
                    partial(_multilateral_final, imputed, cfg.multilateral, method, window))

    if "quality adjustment" in dimensions:
        impact = res.get("quality_adjustment_impact")
        if impact is None:
            not_applicable("quality adjustment", "linked without adjustment; not linked",
                           "no replacement was valued in the quality adjustment ledger, so "
                           "there is no treatment to vary")
        else:
            scenarios = impact.scenarios["final_level"]
            for name in scenarios.index:
                if name != "as_configured":
                    add("quality adjustment", str(name).replace("_", " "),
                        partial(_value, scenarios, name))

    if "imputation method" in dimensions:
        for method in ("none", "class_mean", "carry_forward", "seasonal_hold", "targeted_mean",
                       "overall_mean"):
            if method == cfg.imputation.default_method:
                continue
            add("imputation method", method,
                partial(rerun, imputation__default_method=method))

    if "seasonal treatment" in dimensions:
        try:
            comparison = sn.compare_treatments(imputed, cfg.index, SeasonalConfig(enabled=True))
        except Exception as exc:        # noqa: BLE001
            not_applicable("seasonal treatment", "class confinement; weight update", str(exc))
        else:
            if not len(comparison.seasonal_items):
                not_applicable("seasonal treatment", "weight update",
                               "no item is strictly seasonal, so the treatments cannot differ")
            else:
                # The run keeps each category's weight while its seasonal items
                # are off the shelf -- class confinement at the category level.
                # The treatments are compiled on their own aggregator, whose
                # level is not the run's, so the alternative is the ratio of the
                # two applied to the published headline: what switching to
                # weight update does, on the run's own scale.
                cc = _rebased_final(comparison.results["class_confinement"].headline, reference)
                wu = _rebased_final(comparison.results["weight_update"].headline, reference)
                add("seasonal treatment",
                    "weight update instead of class confinement (ratio of the two treatments "
                    "applied to the published headline)",
                    lambda: base_value * wu / cc)

    table = pd.DataFrame(rows)
    computed = table[table["status"] == "computed"]
    if computed.empty:
        raise ValueError("no alternative could be computed on this data")
    ordered = computed.sort_values("headline")
    lowest, highest = ordered.iloc[0], ordered.iloc[-1]
    return SensitivityResult(
        baseline=base_value, period=period, reference=reference, table=table,
        low=(str(lowest["dimension"]), str(lowest["setting"]), float(lowest["headline"])),
        high=(str(highest["dimension"]), str(highest["setting"]), float(highest["headline"])),
        imputed=published_share)


def _multilateral_final(imputed: pd.DataFrame, settings: Any, method: str, window: int
                        ) -> float:
    from . import multilateral as ml

    configured = settings.model_copy(update={"enabled": True, "method": method,
                                             "window": window})
    return _final(ml.build_multilateral_all(imputed, configured).indices["All items"])


def _value(values: pd.Series, name: Any) -> float:
    return float(values[name])


def _rebased_final(series: pd.Series, reference: pd.Timestamp) -> float:
    """A series' final value with its level at `reference` set to 100, so a
    treatment compiled on its own base reads on the run's scale."""
    clean = series.dropna()
    if reference not in clean.index:
        return float("nan")
    return float(clean.iloc[-1] / clean[reference] * 100.0)
