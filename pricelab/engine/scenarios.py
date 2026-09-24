"""Scenarios: the headline path if stated shocks occur.

A scenario is not a forecast. It says what the headline would do *if* energy
prices, the exchange rate, wages or administered prices moved by stated
amounts at stated times, passing through at stated rates -- and nothing
about whether they will. It is never labelled or exported as a forecast,
and `reporting/charts.check_axes` refuses to draw one beside a forecast
unless each is labelled as what it is.

The assumption list is part of the output, not a footnote. Every shock
carries its size, its timing, how quickly it passes through, and the
coefficient applied with the source of that coefficient. A scenario with
any assumption unstated -- a coefficient with no source, above all -- can
be built and looked at, but `reporting/projections.py` refuses to export it.

The path
--------
The **baseline rule** is stated, not estimated: the headline repeats its
last twelve months' seasonal pattern and grows at its last twelve months'
rate (for a series with no seasonality, it grows at that rate evenly).
Each shock is a permanent change of `size_pct` in its driver's level from
month `start`, passing into the headline level with elasticity
`coefficient`, linearly over `phase_in` months. The scenario path is the
baseline plus the shocks' contributions, in logs.

The **fan** is the spread of the baseline rule's own past errors: the rule
is run from rolling origins over periods already observed, and the fan at
each horizon is the quantiles of its errors there. It measures how far the
headline has strayed from the rule before; it does not include uncertainty
about the shocks or their coefficients, and the label says so. The rule is
also scored against the naive benchmark on the same origins, so a reader
knows whether the baseline itself is any better than no change.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .projection import Assumption, Backtest, BenchmarkComparison, diebold_mariano

__all__ = [
    "BASELINE_RULE",
    "DRIVERS",
    "FAN_LEVELS",
    "ScenarioError",
    "ScenarioResult",
    "Shock",
    "build_scenario",
    "coefficient_from_regression",
    "coefficient_from_weights",
    "scenario_from_spec",
]

DRIVERS: dict[str, str] = {
    "energy prices": "the level of energy prices",
    "exchange rate": "the price of foreign currency (a rise is a depreciation)",
    "wages": "the level of wages",
    "administered prices": "the level of administered (regulated) prices",
}

BASELINE_RULE = ("the headline repeats its last twelve months' seasonal pattern and grows at "
                 "its last twelve months' rate")

FAN_LEVELS: tuple[float, ...] = (0.5, 0.8, 0.95)


class ScenarioError(ValueError):
    """A scenario cannot be built as specified."""


@dataclass(frozen=True)
class Shock:
    """A permanent change in a driver's level, and how it passes into the
    headline. `coefficient` is the elasticity of the headline level to the
    driver level (0.1: a 10% rise in the driver raises the headline by
    1%); `coefficient_source` says where that number came from. Any of the
    numbers may be None, and the source empty, while a scenario is being
    drafted -- the assumption is then unstated and the scenario cannot be
    exported."""

    driver: str
    size_pct: float | None
    start: int | None
    """Months after the last observation at which the shock begins; 1 is the
    first projected month."""
    coefficient: float | None
    coefficient_source: str = ""
    phase_in: int = 1

    def __post_init__(self) -> None:
        if self.driver not in DRIVERS:
            raise ScenarioError(f"unknown driver {self.driver!r}; one of {sorted(DRIVERS)}")
        if self.phase_in < 1:
            raise ScenarioError("a shock passes through over at least one month")
        if self.start is not None and self.start < 1:
            raise ScenarioError("a shock starts in a projected month (1 or later)")

    @property
    def name(self) -> str:
        return f"shock: {self.driver}"

    def contribution(self, horizon: int) -> np.ndarray:
        """The shock's contribution to the log headline x 100 at each
        projected month (zero where a number is unstated)."""
        if self.size_pct is None or self.start is None or self.coefficient is None:
            return np.zeros(horizon)
        months = np.arange(1, horizon + 1)
        built = np.clip((months - self.start + 1) / self.phase_in, 0.0, 1.0)
        return np.asarray(100 * np.log1p(self.size_pct / 100) * self.coefficient * built,
                          dtype=float)

    def assumptions(self) -> list[Assumption]:
        def num(value: float | None, fmt: str) -> str:
            return "" if value is None else fmt.format(value)
        return [
            Assumption(f"{self.driver}: size of the shock",
                       num(self.size_pct, "{:+.1f}% in " + DRIVERS[self.driver]),
                       "the scenario's author" if self.size_pct is not None else ""),
            Assumption(f"{self.driver}: timing",
                       "" if self.start is None else
                       f"from projected month {self.start}, permanent, passing through over "
                       f"{self.phase_in} month{'s' if self.phase_in != 1 else ''}",
                       "the scenario's author" if self.start is not None else ""),
            Assumption(f"{self.driver}: coefficient applied",
                       num(self.coefficient, "elasticity {:.4f} of the headline level to the "
                           "driver level"),
                       self.coefficient_source.strip()),
        ]


def coefficient_from_weights(res: dict[str, Any], categories: list[str], label: str = ""
                             ) -> tuple[float, str]:
    """The direct, first-round coefficient for a driver whose prices are
    categories of the index itself (energy, administered prices): their
    share of the aggregate. Returns (coefficient, source)."""
    from .index import category_weights

    columns = [c for c in res["indices"].columns if c != "All items"]
    unknown = sorted(set(categories) - set(columns))
    if unknown or not categories:
        raise ScenarioError(f"not categories of this run: {unknown or '(none chosen)'}")
    weights = category_weights(res["imputed"])
    if weights is None:
        share = len(categories) / len(columns)
        basis = "equal category weights, as the run has no expenditure weights"
    else:
        total = sum(weights.get(c, 0.0) for c in columns)
        share = sum(weights.get(c, 0.0) for c in categories) / total
        basis = "the run's expenditure weights"
    source = (f"direct effect only: the share of {', '.join(categories)} in the aggregate "
              f"({share:.1%}, {basis}{', run ' + label if label else ''}); second-round "
              "effects are not included")
    return share, source


def coefficient_from_regression(result: Any) -> tuple[float, str]:
    """The long-run coefficient of a pass-through regression, as an
    elasticity, with its source -- including that it is correlational."""
    regression = getattr(result, "regression", None)
    if regression is None or getattr(result, "method", "") != "pass_through":
        raise ScenarioError("only a pass-through regression supplies a pass-through coefficient")
    driver = result.spec.driver_name or "the driver"
    history = result.history
    source = (f"long-run pass-through from the pass-through regression on {driver} "
              f"({history.index[0]:%b %Y} to {history.index[-1]:%b %Y}, R-squared "
              f"{regression.r_squared:.2f}); correlational, not causal")
    return float(regression.long_run), source


@dataclass(frozen=True)
class ScenarioResult:
    """A scenario, with the four parts every projection carries."""

    name: str
    series: str
    history: pd.Series
    path: pd.DataFrame
    """Index period; point (the scenario path), lower and upper (the 95%
    fan), lower_80, upper_80, lower_50, upper_50, baseline, and one
    `shock: <driver>` column per shock (its contribution, % of the level)."""
    shocks: tuple[Shock, ...]
    backtest: Backtest
    benchmark: BenchmarkComparison
    assumptions: tuple[Assumption, ...]
    seasonal_period: int = 12
    notes: tuple[str, ...] = field(default_factory=tuple)

    kind = "scenario"

    @property
    def spec_json(self) -> str:
        """Everything that determines the scenario given its series, for
        the registry."""
        return json.dumps({"name": self.name, "horizon": len(self.path),
                           "origins": self.backtest.origins,
                           "seasonal_period": self.seasonal_period,
                           "shocks": [asdict(s) for s in self.shocks]}, sort_keys=True)

    @property
    def unstated(self) -> list[str]:
        return [a.name for a in self.assumptions if not a.stated]

    @property
    def label(self) -> str:
        last = self.path.iloc[-1]
        period = self.path.index[-1]
        shocks = "; ".join(
            f"{s.driver} {s.size_pct:+.1f}% from month {s.start}" if s.size_pct is not None
            and s.start is not None else f"{s.driver} (unstated)" for s in self.shocks)
        text = (f"Scenario -- not a forecast. {self.name}: if {shocks or 'no shock occurs'}, "
                f"{self.series} would reach {last['point']:.2f} in {period:%b %Y}, against "
                f"{last['baseline']:.2f} under the baseline rule ({BASELINE_RULE}). The 95% fan, "
                f"{last['lower']:.2f} to {last['upper']:.2f}, is the spread of the baseline "
                "rule's past errors; it does not include uncertainty about the shocks or their "
                f"coefficients. {self.benchmark.statement} {self.backtest.statement}")
        if self.unstated:
            text += (" Unstated assumptions: " + ", ".join(self.unstated)
                     + ". Until they are stated this scenario cannot be exported.")
        return text


def _baseline(train: np.ndarray, horizon: int, period: int, seasonal: bool) -> np.ndarray:
    """The baseline rule in logs: the value a whole number of seasons back
    plus that many years' growth at the last twelve months' rate (evenly
    spread when the series is not seasonal)."""
    growth = train[-1] - train[-1 - period]
    steps = np.arange(1, horizon + 1)
    if not seasonal:
        return np.asarray(train[-1] + growth * steps / period, dtype=float)
    years = np.ceil(steps / period).astype(int)
    return np.array([train[len(train) - period * k + h - 1] + k * growth
                     for h, k in zip(steps, years, strict=True)])


def build_scenario(series: pd.Series, shocks: list[Shock] | tuple[Shock, ...], *,
                   name: str = "Scenario", horizon: int = 24, origins: int = 36,
                   seasonal_period: int = 12) -> ScenarioResult:
    """The headline path under `shocks`, its fan, and its assumptions."""
    from .forecasting import _benchmark, _monthly, is_seasonal, seasonal_strength

    level = _monthly(series)
    y = np.log(level.to_numpy())
    n, m = len(y), seasonal_period
    first = n - horizon - origins + 1
    if first < 2 * m + 1:
        raise ScenarioError(
            f"{n} periods are too few for {origins} backtest origins at horizon {horizon}")
    seasonal = is_seasonal(pd.Series(y), m)
    benchmark_name = "seasonal naive" if seasonal else "random walk"

    rows = []
    for origin in range(first, n - horizon + 1):
        train = y[:origin]
        rule = _baseline(train, horizon, m, seasonal)
        bench = _benchmark(train, horizon, m, seasonal)
        for h in range(1, horizon + 1):
            actual = y[origin + h - 1]
            rows.append({"origin": level.index[origin - 1], "h": h,
                         "target": level.index[origin + h - 1], "actual": actual,
                         "method": rule[h - 1], "benchmark": bench[h - 1],
                         "lower": np.nan, "upper": np.nan,
                         "error_pct": (actual - rule[h - 1]) * 100,
                         "benchmark_error_pct": (actual - bench[h - 1]) * 100,
                         "inside": False})
    errors = pd.DataFrame(rows)
    backtest = Backtest(
        errors=errors, level=0.95, method="the baseline rule",
        interval_note=("The fan is built from these errors, so it matches them by construction; "
                       "no separate check of the fan against them is possible."))
    per_origin = errors.groupby("origin")[["error_pct", "benchmark_error_pct"]].apply(
        lambda g: pd.Series({"m": float(np.mean(g["error_pct"] ** 2)),
                             "b": float(np.mean(g["benchmark_error_pct"] ** 2))}))
    stat, p_value = diebold_mariano(per_origin["m"].to_numpy(), per_origin["b"].to_numpy(),
                                    horizon)
    table = backtest.by_horizon
    benchmark = BenchmarkComparison(
        benchmark=benchmark_name, method="The baseline rule", method_rmse_pct=backtest.rmse_pct,
        benchmark_rmse_pct=float(np.sqrt(np.mean(errors["benchmark_error_pct"] ** 2))),
        dm_statistic=stat, p_value=p_value, origins=backtest.origins, horizon=horizon,
        horizons_better=int((table["rmse_pct"] < table["benchmark_rmse_pct"]).sum()))

    base = _baseline(y, horizon, m, seasonal)
    contributions = {s.name: s.contribution(horizon) for s in shocks}
    total = np.sum(list(contributions.values()), axis=0) if contributions else np.zeros(horizon)
    point = base + total / 100
    future = pd.date_range(level.index[-1] + pd.offsets.MonthBegin(1), periods=horizon,
                           freq="MS")
    columns: dict[str, np.ndarray] = {"point": np.exp(point)}
    by_h = errors.groupby("h")["error_pct"]
    for fan in FAN_LEVELS:
        lo = by_h.quantile((1 - fan) / 2).to_numpy() / 100
        hi = by_h.quantile((1 + fan) / 2).to_numpy() / 100
        suffix = "" if fan == 0.95 else f"_{int(fan * 100)}"
        columns[f"lower{suffix}"] = np.exp(point + lo)
        columns[f"upper{suffix}"] = np.exp(point + hi)
    columns["baseline"] = np.exp(base)
    for key, value in contributions.items():
        columns[key] = value
    path = pd.DataFrame(columns, index=future)
    path.index.name = "period"

    growth = (y[-1] - y[-1 - m]) * 100
    assumptions = [
        Assumption("Baseline rule", f"{BASELINE_RULE} ({growth:+.2f} log points over the last "
                   f"twelve months; the series is {'seasonal' if seasonal else 'not seasonal'}, "
                   f"STL seasonal strength {seasonal_strength(pd.Series(y), m):.2f})",
                   "PriceLab scenario convention (docs/methodology/scenarios.md)"),
        Assumption("Starting point", f"{level.index[-1]:%b %Y}, {level.iloc[-1]:.2f}",
                   "the compiled run's headline series"),
        Assumption("Fan", f"quantiles of the baseline rule's errors over {backtest.origins} "
                   f"rolling origins at each horizon ({', '.join(f'{f:.0%}' for f in FAN_LEVELS)}"
                   "); excludes uncertainty about the shocks and their coefficients",
                   "the backtest of the baseline rule"),
        Assumption("How a shock passes through", "a permanent change in the driver's level, "
                   "passing into the headline level in proportion to the coefficient, linearly "
                   "over the phase-in months; shocks add in logs",
                   "PriceLab scenario convention (docs/methodology/scenarios.md)"),
    ]
    for shock in shocks:
        assumptions.extend(shock.assumptions())
    return ScenarioResult(name=name, series=str(series.name or "headline"), history=level,
                          path=path, shocks=tuple(shocks), backtest=backtest,
                          benchmark=benchmark, assumptions=tuple(assumptions),
                          seasonal_period=m)


def scenario_from_spec(series: pd.Series, spec_json: str) -> ScenarioResult:
    """Rebuild a scenario from `ScenarioResult.spec_json`."""
    spec = json.loads(spec_json)
    return build_scenario(series, [Shock(**shock) for shock in spec["shocks"]],
                          name=spec["name"], horizon=spec["horizon"], origins=spec["origins"],
                          seasonal_period=spec["seasonal_period"])
