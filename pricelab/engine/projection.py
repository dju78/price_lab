"""What every projected figure carries, forecast or scenario alike.

A projected number is the one number in the platform a reader will take as
a fact about the future rather than a statement about a model. So none
leaves the system alone. Each carries four things, and `missing_parts`
names any that are absent:

1. **its interval** -- the model-implied interval for a forecast, the fan
   for a scenario;
2. **its backtest performance** -- the measured error of the same method
   forecasting periods already observed, from rolling origins;
3. **its benchmark comparison** -- the same backtest for a naive benchmark
   (a random walk for a level, the seasonal naive for a seasonal series),
   on the same out-of-sample window, and whether the method beats it;
4. **its assumptions** -- every one, each with its value and its source.

`reporting/projections.py` refuses to export a projection with any part
missing; `tests/test_forecasting.py` holds that for every export path.

A forecast and a scenario are different things. A forecast is a model's
statement about what is likely; a scenario is what follows *if* stated
assumptions hold, and says nothing about whether they will. `KINDS` names
the two, and neither is ever labelled as the other.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
import pandas as pd

__all__ = [
    "KINDS",
    "Assumption",
    "Backtest",
    "BenchmarkComparison",
    "Projection",
    "ProjectionIncomplete",
    "digest",
    "missing_parts",
]

KINDS: tuple[str, ...] = ("forecast", "scenario")

#: The four parts, in the words the refusals use.
PARTS: tuple[str, ...] = ("interval", "backtest performance", "benchmark comparison",
                          "assumptions")


class ProjectionIncomplete(ValueError):
    """A projection lacks one of the four parts it must carry to leave."""


@dataclass(frozen=True)
class Assumption:
    """One assumption a projection rests on: what, its value, and where
    that value came from. An assumption with no value or no source is
    unstated, and a projection carrying one cannot be exported."""

    name: str
    value: str
    source: str

    @property
    def stated(self) -> bool:
        return bool(self.name.strip() and self.value.strip() and self.source.strip())

    def as_row(self) -> dict[str, str]:
        return {"assumption": self.name, "value": self.value, "source": self.source}


@dataclass(frozen=True)
class BenchmarkComparison:
    """The method against a naive benchmark on the same backtest origins
    and horizons.

    `beats` only when the method's root mean squared error is lower *and*
    a Diebold-Mariano test (Harvey-Leybourne-Newbold small-sample form,
    one-sided) rejects equal accuracy at 5%: a method that is slightly
    better by luck on 24 origins has not beaten anything."""

    benchmark: str
    method: str
    method_rmse_pct: float
    benchmark_rmse_pct: float
    dm_statistic: float
    p_value: float
    origins: int
    horizon: int
    horizons_better: int = 0
    """Horizons at which the method's RMSE is below the benchmark's."""

    @property
    def ratio(self) -> float:
        """Method RMSE over benchmark RMSE: below 1 is better."""
        return self.method_rmse_pct / self.benchmark_rmse_pct

    @property
    def beats(self) -> bool:
        return bool(self.ratio < 1.0 and self.p_value < 0.05)

    @property
    def statement(self) -> str:
        return self._verdict() + (
            f" By horizon, its RMSE is the lower at {self.horizons_better} of {self.horizon}.")

    def _verdict(self) -> str:
        window = (f"on the same out-of-sample window ({self.origins} rolling origins, "
                  f"horizons 1-{self.horizon})")
        change = abs(1.0 - self.ratio)
        if self.beats:
            return (f"{self.method} beats the {self.benchmark} benchmark {window}: backtest "
                    f"RMSE {self.method_rmse_pct:.2f}% against {self.benchmark_rmse_pct:.2f}%, "
                    f"{change:.0%} lower (Diebold-Mariano p = {self.p_value:.3f}).")
        if self.ratio < 1.0:
            return (f"{self.method} does not beat the {self.benchmark} benchmark {window}: its "
                    f"backtest RMSE of {self.method_rmse_pct:.2f}% is {change:.0%} below the "
                    f"benchmark's {self.benchmark_rmse_pct:.2f}%, but the difference is not "
                    f"distinguishable from chance (Diebold-Mariano p = {self.p_value:.3f}).")
        return (f"{self.method} does not beat the {self.benchmark} benchmark {window}: its "
                f"backtest RMSE of {self.method_rmse_pct:.2f}% is {change:.0%} above the "
                f"benchmark's {self.benchmark_rmse_pct:.2f}%. The naive benchmark is the "
                "more accurate here.")


@dataclass(frozen=True)
class Backtest:
    """Rolling-origin backtest: at each origin the method is refitted on the
    data up to that origin and forecasts the next `horizon` periods, which
    are already observed. Errors are in log points x 100 -- approximately
    percentage errors in the level.

    `errors` has one row per origin and horizon: origin, h, target, actual,
    method, benchmark, lower, upper (all in logs), error_pct,
    benchmark_error_pct, inside.

    The comparison with the model-implied interval: at each horizon the
    interval the model claimed (its mean half-width across origins) is set
    against the half-width the measured errors imply at the same level
    (z times the backtest RMSE). Where the second is larger, the model is
    understating its own uncertainty."""

    errors: pd.DataFrame
    level: float
    method: str
    interval_note: str = ""
    """Set when the projection's interval is built from these very errors
    (a scenario's fan), so comparing the two would be circular; the note
    says so in place of the comparison."""

    @property
    def origins(self) -> int:
        return int(self.errors["origin"].nunique())

    @property
    def horizon(self) -> int:
        return int(self.errors["h"].max())

    @property
    def by_horizon(self) -> pd.DataFrame:
        from scipy.stats import norm

        z = float(norm.ppf(0.5 + self.level / 2))
        halfwidth = (self.errors["upper"] - self.errors["lower"]) / 2 * 100
        grouped = self.errors.assign(_halfwidth=halfwidth).groupby("h")
        table = pd.DataFrame({
            "rmse_pct": grouped["error_pct"].apply(lambda e: float(np.sqrt(np.mean(e ** 2)))),
            "benchmark_rmse_pct": grouped["benchmark_error_pct"].apply(
                lambda e: float(np.sqrt(np.mean(e ** 2)))),
            "coverage": grouped["inside"].mean(),
            "implied_halfwidth_pct": grouped["_halfwidth"].mean(),
        })
        table["measured_halfwidth_pct"] = z * table["rmse_pct"]
        table["measured_over_implied"] = (table["measured_halfwidth_pct"]
                                          / table["implied_halfwidth_pct"])
        return table

    @property
    def rmse_pct(self) -> float:
        return float(np.sqrt(np.mean(self.errors["error_pct"] ** 2)))

    @property
    def coverage(self) -> float:
        return float(self.errors["inside"].mean())

    @property
    def understated_horizons(self) -> list[int]:
        if self.interval_note:
            return []
        table = self.by_horizon
        return [int(h) for h in table.index[table["measured_over_implied"] > 1.0]]

    @property
    def understates(self) -> bool:
        return bool(self.understated_horizons)

    @property
    def statement(self) -> str:
        table = self.by_horizon
        head = (f"Backtest of {self.method} ({self.origins} rolling origins, horizons "
                f"1-{self.horizon}): RMSE {self.rmse_pct:.2f}%, from "
                f"{table['rmse_pct'].iloc[0]:.2f}% one period ahead to "
                f"{table['rmse_pct'].iloc[-1]:.2f}% at {self.horizon}")
        if self.interval_note:
            return f"{head}. {self.interval_note}"
        head += (f"; the {self.level:.0%} model-implied interval contained the outcome "
                 f"{self.coverage:.0%} of the time.")
        bad = self.understated_horizons
        if not bad:
            return head + (" At every horizon the measured error is within the model-implied "
                           "interval.")
        worst = table.reset_index().sort_values("measured_over_implied").iloc[-1]
        return head + (
            f" The backtest error exceeds the model-implied interval at {len(bad)} of "
            f"{self.horizon} horizons (worst at {int(worst['h'])}: the measured error implies "
            f"a half-width of {worst['measured_halfwidth_pct']:.2f}% against the "
            f"{worst['implied_halfwidth_pct']:.2f}% the model claims, "
            f"{worst['measured_over_implied']:.2f} times wider). The model is "
            "understating its own uncertainty; read its interval as too narrow.")

    @property
    def digest(self) -> str:
        return digest(self.errors)


class Projection(Protocol):
    """What `reporting/projections.py` exports: a forecast or a scenario."""

    kind: str

    @property
    def path(self) -> pd.DataFrame: ...

    @property
    def backtest(self) -> Backtest | None: ...

    @property
    def benchmark(self) -> BenchmarkComparison | None: ...

    @property
    def assumptions(self) -> tuple[Assumption, ...]: ...

    @property
    def label(self) -> str: ...


def missing_parts(projection: Any) -> list[str]:
    """The parts of the four this projection lacks, in words; empty when it
    may leave. `path` must hold `point`, `lower` and `upper` columns with a
    finite interval at every period; every assumption must be stated."""
    missing: list[str] = []
    path = getattr(projection, "path", None)
    if (not isinstance(path, pd.DataFrame) or path.empty
            or not {"point", "lower", "upper"} <= set(path.columns)
            or not np.isfinite(path[["lower", "upper"]].to_numpy(dtype=float)).all()):
        missing.append("interval")
    backtest = getattr(projection, "backtest", None)
    if backtest is None or backtest.errors.empty:
        missing.append("backtest performance")
    if getattr(projection, "benchmark", None) is None:
        missing.append("benchmark comparison")
    assumptions = tuple(getattr(projection, "assumptions", ()) or ())
    if not assumptions:
        missing.append("assumptions")
    else:
        unstated = [a.name or "(unnamed)" for a in assumptions if not a.stated]
        if unstated:
            missing.append("assumptions (unstated: " + ", ".join(unstated) + ")")
    if getattr(projection, "kind", None) not in KINDS:
        missing.append("kind (a forecast or a scenario)")
    return missing


def digest(frame: pd.DataFrame) -> str:
    """A content hash of a frame, for reproducibility checks: the same
    numbers to the last bit give the same digest."""
    hashed = pd.util.hash_pandas_object(frame.reset_index(drop=True), index=False)
    return hashlib.sha256(hashed.to_numpy().tobytes()).hexdigest()


def diebold_mariano(loss_method: np.ndarray, loss_benchmark: np.ndarray, horizon: int
                    ) -> tuple[float, float]:
    """One-sided Diebold-Mariano test that the method's loss is lower, with
    the Harvey-Leybourne-Newbold small-sample correction and a Newey-West
    long-run variance at `horizon - 1` lags (consecutive origins' multi-step
    errors overlap). Returns (statistic, p-value); a positive statistic
    favours the method."""
    from scipy.stats import t as student

    d = np.asarray(loss_benchmark, dtype=float) - np.asarray(loss_method, dtype=float)
    n = len(d)
    if n < 3:
        return float("nan"), 1.0
    mean = d.mean()
    centred = d - mean
    lags = max(0, min(horizon - 1, n - 2))
    variance = float(centred @ centred) / n
    for k in range(1, lags + 1):
        variance += 2 * (1 - k / (lags + 1)) * float(centred[k:] @ centred[:-k]) / n
    if variance <= 0:
        return float("nan"), 1.0 if mean <= 0 else 0.0
    stat = mean / np.sqrt(variance / n)
    h = horizon
    correction = np.sqrt((n + 1 - 2 * h + h * (h - 1) / n) / n) if n + 1 - 2 * h > 0 else 1.0
    stat *= correction
    return float(stat), float(student.sf(stat, df=n - 1))
