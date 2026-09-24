"""Forecasting the headline: ARIMA and SARIMAX with order selection and
residual diagnostics, exponential smoothing (ETS), and pass-through and
Phillips-curve regressions.

Every forecast is a statement about a model, and three rules keep it one.

**Against a naive benchmark.** Every method is backtested from rolling
origins, and a naive benchmark -- a random walk for a level, the seasonal
naive for a seasonal series -- is scored on exactly the same origins and
horizons. Whether the method beats it is part of the forecast's label, in
the same sentence as the number, not in a diagnostic panel: a model that
loses to a random walk is a finding. "Beats" means lower error *and* a
Diebold-Mariano test rejecting equal accuracy at 5%
(`projection.BenchmarkComparison`).

**Interval and measured error, both.** Every forecast carries the interval
its model implies and the error its backtest measured. Where the measured
error is wider than the implied interval at some horizon, the label says the
model is understating its own uncertainty -- the most common way a forecast
misleads (`projection.Backtest`).

**Regressions are correlational.** The pass-through and Phillips-curve
specifications are regressions of the headline's rate of change on a
driver's lagged values. Their coefficients describe how the two have moved
together in this sample; they are not estimates of what a change in the
driver would do to prices, and every place a coefficient appears carries
`CORRELATIONAL` saying so.

The series is modelled in logs, so errors are in log points x 100
(approximately percentage errors) and the point path is the median. An
ARIMA or ETS specification is selected on the data before the backtest
window opens and then held fixed, so the backtest is out of sample in its
specification as well as its parameters; the final forecast refits that
specification on all the data.
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from itertools import product
from typing import Any

import numpy as np
import pandas as pd

from .projection import Assumption, Backtest, BenchmarkComparison, diebold_mariano

__all__ = [
    "CORRELATIONAL",
    "METHODS",
    "SEASONAL_STRENGTH_THRESHOLD",
    "ForecastError",
    "ForecastResult",
    "ForecastSpec",
    "RegressionFit",
    "forecast",
    "is_seasonal",
    "seasonal_strength",
]

METHODS: dict[str, str] = {
    "arima": "ARIMA",
    "sarimax": "SARIMAX",
    "ets": "Exponential smoothing (ETS)",
    "pass_through": "Pass-through regression",
    "phillips": "Phillips-curve regression",
}

#: Hyndman and Athanasopoulos's rule for a seasonal difference: STL seasonal
#: strength above 0.64 (the threshold `nsdiffs` uses).
SEASONAL_STRENGTH_THRESHOLD = 0.64

CORRELATIONAL = ("Correlational, not causal: the coefficients describe how the headline's "
                 "rate of change and the driver have moved together in this sample. They are "
                 "not estimates of what a change in the driver would do to prices.")


class ForecastError(ValueError):
    """A forecast cannot be produced as specified."""


@dataclass(frozen=True)
class ForecastSpec:
    """Everything that determines a forecast, so it can be registered and
    reproduced. `driver` is a period-indexed series, needed by the
    regressions and optional for SARIMAX."""

    method: str = "arima"
    horizon: int = 12
    origins: int = 24
    level: float = 0.95
    seasonal_period: int = 12
    max_order: int = 2
    lags: int = 3
    driver_name: str = ""
    driver_source: str = ""
    simulations: int = 2000
    seed: int = 0

    def to_json(self) -> str:
        return json.dumps(self.__dict__, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> ForecastSpec:
        return cls(**json.loads(text))


@dataclass(frozen=True)
class RegressionFit:
    """A pass-through or Phillips-curve regression: coefficients with HAC
    standard errors, and the long-run sum. Correlational (`reading`)."""

    coefficients: pd.DataFrame
    """term, estimate, std_error, p_value, reading."""
    long_run: float
    r_squared: float
    observations: int
    reading: str = CORRELATIONAL


@dataclass(frozen=True)
class ForecastResult:
    """A forecast, with the four parts every projection carries."""

    series: str
    method: str
    spec: ForecastSpec
    history: pd.Series
    path: pd.DataFrame
    """Index period; point, lower, upper (the model-implied interval), and
    measured_lower, measured_upper (the interval the backtest errors imply),
    all as index levels."""
    backtest: Backtest
    benchmark: BenchmarkComparison
    assumptions: tuple[Assumption, ...]
    specification: str
    diagnostics: pd.DataFrame
    """test, statistic, p_value, verdict."""
    regression: RegressionFit | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    parameters: dict[str, float] = field(default_factory=dict)
    """The selected specification's parameters, fitted on all the data."""

    kind = "forecast"

    @property
    def beats_benchmark(self) -> bool:
        return self.benchmark.beats

    @property
    def headline(self) -> str:
        last = self.path.iloc[-1]
        return (f"Forecast ({self.specification}) of {self.series} at "
                f"{self.path.index[-1]:%b %Y}: {last['point']:.2f}, {self.spec.level:.0%} "
                f"model-implied interval {last['lower']:.2f} to {last['upper']:.2f}; the "
                f"backtest errors imply {last['measured_lower']:.2f} to "
                f"{last['measured_upper']:.2f}.")

    @property
    def label(self) -> str:
        """The forecast, its benchmark verdict, and its interval check, in
        one statement: the reader of the number reads all three."""
        parts = [self.headline, self.benchmark.statement, self.backtest.statement]
        if self.regression is not None:
            parts.append(self.regression.reading)
        return " ".join(parts)

    @property
    def verdict(self) -> str:
        """The short form for a chart title."""
        if self.beats_benchmark:
            return f"beats the {self.benchmark.benchmark} benchmark"
        return f"does not beat the {self.benchmark.benchmark} benchmark"


# ---------------------------------------------------------------------
# Seasonality and the benchmark
# ---------------------------------------------------------------------
def seasonal_strength(log_level: pd.Series, period: int = 12) -> float:
    """STL seasonal strength, max(0, 1 - var(remainder) / var(remainder +
    seasonal)) (Wang, Smith and Hyndman 2006)."""
    from statsmodels.tsa.seasonal import STL

    clean = log_level.dropna()
    if len(clean) < 2 * period + 1:
        return 0.0
    fit = STL(clean.to_numpy(), period=period, robust=True).fit()
    detrended = fit.resid + fit.seasonal
    return float(max(0.0, 1.0 - np.var(fit.resid) / np.var(detrended)))


def is_seasonal(log_level: pd.Series, period: int = 12) -> bool:
    return period > 1 and seasonal_strength(log_level, period) > SEASONAL_STRENGTH_THRESHOLD


def _benchmark(train: np.ndarray, horizon: int, period: int, seasonal: bool) -> np.ndarray:
    """Random walk (the last value) for a level; seasonal naive (the value a
    whole number of seasons back) for a seasonal series."""
    if not seasonal:
        return np.full(horizon, train[-1])
    steps = np.arange(1, horizon + 1)
    back = period * np.ceil(steps / period).astype(int)
    return np.array([train[len(train) - b + h - 1] for h, b in zip(steps, back, strict=True)])


# ---------------------------------------------------------------------
# Models: each is a (fit on a training array) -> forecaster closure
# ---------------------------------------------------------------------
Forecaster = Callable[[int, float], tuple[np.ndarray, np.ndarray, np.ndarray]]
"""(horizon, level) -> (mean, lower, upper), in logs."""


@dataclass(frozen=True)
class _Model:
    name: str
    description: str
    fit: Callable[[np.ndarray, np.ndarray | None], Forecaster]
    residuals: Callable[[np.ndarray, np.ndarray | None], np.ndarray]
    params: dict[str, float]


def _quiet(fn: Callable[[], Any]) -> Any:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return fn()


def _kpss_p(kpss: Any, x: np.ndarray) -> float:
    return float(kpss(x, regression="c", nlags="auto")[1])


def _kpss_differences(y: np.ndarray, max_d: int = 2) -> int:
    """Differences needed for level stationarity, by repeated KPSS tests at
    5% (the `ndiffs` rule)."""
    from statsmodels.tsa.stattools import kpss

    d, x = 0, np.asarray(y, dtype=float)
    while d < max_d and len(x) > 10:
        p_value = float(_quiet(partial(_kpss_p, kpss, x)))
        if p_value >= 0.05:
            break
        x, d = np.diff(x), d + 1
    return d


def _fit_sarimax(model: Any, y: np.ndarray, exog: np.ndarray | None,
                 order: tuple[int, int, int], sorder: tuple[int, int, int, int], trend: str
                 ) -> Any:
    return model(y, exog=exog, order=order, seasonal_order=sorder, trend=trend).fit(disp=False)


def _sarimax_spec(y: np.ndarray, seasonal: bool, period: int, max_order: int,
                  exog: np.ndarray | None) -> tuple[tuple[int, int, int],
                                                    tuple[int, int, int, int], str, float]:
    """Order selection by AICc over p, q <= max_order and, for a seasonal
    series, P, Q <= 1 with one seasonal difference; d by KPSS."""
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    D = 1 if seasonal else 0
    base = y[period:] - y[:-period] if D else y
    d = _kpss_differences(base, max_d=1 if D else 2)
    # statsmodels' SARIMAX applies the trend polynomial to the differenced
    # series, so after one difference a constant is a drift in the level;
    # after two, any constant would be a quadratic trend and is left out.
    trend = "c" if d + D <= 1 else "n"
    best: tuple[float, tuple[int, int, int], tuple[int, int, int, int]] | None = None
    seasonal_grid = list(product((0, 1), (0, 1))) if seasonal else [(0, 0)]
    for p, q in product(range(max_order + 1), range(max_order + 1)):
        for P, Q in seasonal_grid:
            order, sorder = (p, d, q), (P, D, Q, period if seasonal else 0)
            try:
                res = _quiet(partial(_fit_sarimax, SARIMAX, y, exog, order, sorder, trend))
            except Exception:           # noqa: BLE001 - an order that cannot be fitted is skipped
                continue
            score = float(res.aicc)
            if np.isfinite(score) and (best is None or score < best[0]):
                best = (score, order, sorder)
    if best is None:
        raise ForecastError("no ARIMA order could be fitted to this series")
    return best[1], best[2], trend, best[0]


def _sarimax_model(y: np.ndarray, spec: ForecastSpec, seasonal: bool,
                   exog: np.ndarray | None,
                   order: tuple[int, int, int] | None = None,
                   sorder: tuple[int, int, int, int] | None = None, trend: str | None = None
                   ) -> _Model:
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    if order is None or sorder is None or trend is None:
        order, sorder, trend, _ = _sarimax_spec(y, seasonal, spec.seasonal_period,
                                                spec.max_order, exog)
    fixed_order, fixed_sorder, fixed_trend = order, sorder, trend

    def fit(train: np.ndarray, x: np.ndarray | None) -> Forecaster:
        res = _quiet(lambda: SARIMAX(train, exog=x, order=fixed_order,
                                     seasonal_order=fixed_sorder, trend=fixed_trend
                                     ).fit(disp=False))

        def predict(horizon: int, level: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
            # Beyond the origin the driver is held at its last observed
            # value: at a backtest origin its later values were not known.
            future = None if x is None else np.full((horizon, 1), float(x[-1]))
            fc = res.get_forecast(horizon, exog=future)
            bounds = np.asarray(fc.conf_int(alpha=1 - level))
            return np.asarray(fc.predicted_mean), bounds[:, 0], bounds[:, 1]
        return predict

    def residuals(train: np.ndarray, x: np.ndarray | None) -> np.ndarray:
        res = _quiet(lambda: SARIMAX(train, exog=x, order=fixed_order,
                                     seasonal_order=fixed_sorder, trend=fixed_trend
                                     ).fit(disp=False))
        skip = fixed_order[1] + fixed_sorder[1] * max(fixed_sorder[3], 1)
        return np.asarray(res.resid)[skip:]

    full = _quiet(lambda: SARIMAX(y, exog=exog, order=fixed_order, seasonal_order=fixed_sorder,
                                  trend=fixed_trend).fit(disp=False))
    params = {str(k): float(v) for k, v in zip(full.param_names, full.params, strict=True)}
    p, d, q = fixed_order
    P, D, Q, m = fixed_sorder
    name = f"ARIMA({p},{d},{q})" + (f"({P},{D},{Q})[{m}]" if m else "")
    if exog is not None:
        name = name.replace("ARIMA", "ARIMAX") if not m else name.replace("ARIMA", "SARIMAX")
    elif m:
        name = "S" + name
    differences = fixed_order[1] + fixed_sorder[1]
    drift = "" if fixed_trend == "n" else (" with drift" if differences else " with a constant")
    return _Model(name + drift, f"{name}{drift}, order chosen by AICc", fit, residuals, params)


def _ets_model(selection: np.ndarray, y: np.ndarray, spec: ForecastSpec, seasonal: bool
               ) -> _Model:
    """Additive-error ETS on the log level, trend none / additive / damped,
    additive seasonality when the series is seasonal; chosen by AICc on
    `selection`, its parameters reported as fitted on `y`."""
    from statsmodels.tsa.exponential_smoothing.ets import ETSModel

    candidates: list[tuple[str | None, bool, str | None]] = [
        (trend, damped, season)
        for trend, damped in ((None, False), ("add", False), ("add", True))
        for season in (("add",) if seasonal else (None,))]
    period = spec.seasonal_period if seasonal else None

    def build(train: np.ndarray, trend: str | None, damped: bool, season: str | None) -> Any:
        # A dated series: statsmodels' ETS prediction frame needs an index.
        dated = pd.Series(train, index=pd.date_range("2000-01-01", periods=len(train),
                                                     freq="MS"))
        return _quiet(lambda: ETSModel(dated, error="add", trend=trend, damped_trend=damped,
                                       seasonal=season, seasonal_periods=period
                                       ).fit(disp=False, maxiter=2000))

    best: tuple[float, tuple[str | None, bool, str | None]] | None = None
    for candidate in candidates:
        try:
            score = float(build(selection, *candidate).aicc)
        except Exception:               # noqa: BLE001
            continue
        if np.isfinite(score) and (best is None or score < best[0]):
            best = (score, candidate)
    if best is None:
        raise ForecastError("no exponential smoothing model could be fitted to this series")
    chosen = best[1]

    def fit(train: np.ndarray, _x: np.ndarray | None) -> Forecaster:
        res = build(train, *chosen)

        def predict(horizon: int, level: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
            frame = res.get_prediction(start=len(train), end=len(train) + horizon - 1
                                       ).summary_frame(alpha=1 - level)
            return (frame["mean"].to_numpy(), frame["pi_lower"].to_numpy(),
                    frame["pi_upper"].to_numpy())
        return predict

    def residuals(train: np.ndarray, _x: np.ndarray | None) -> np.ndarray:
        return np.asarray(build(train, *chosen).resid)

    full = build(y, *chosen)
    params = {str(k): float(v) for k, v in zip(full.param_names, full.params, strict=True)
              if not str(k).startswith("initial")}
    trend, damped, season = chosen
    code = f"ETS(A,{'N' if trend is None else ('Ad' if damped else 'A')},{'A' if season else 'N'})"
    return _Model(code, f"{code} on the log level, chosen by AICc", fit, residuals, params)


# ---------------------------------------------------------------------
# Regressions: pass-through and Phillips curve
# ---------------------------------------------------------------------
def _regression_design(y: np.ndarray, x: np.ndarray, spec: ForecastSpec, seasonal: bool,
                       months: np.ndarray) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """pi_t = a + seasonal dummies + rho pi_{t-1} + sum_j b_j z_{t-j} + e,
    with pi the monthly log change x 100 and z the driver's monthly log
    change x 100 (pass-through) or its level (Phillips curve)."""
    pi = np.diff(y) * 100
    z = (np.diff(np.log(x)) * 100) if spec.method == "pass_through" else x[1:]
    L = spec.lags
    rows, target = [], []
    for t in range(L, len(pi)):
        row = [1.0, pi[t - 1]] + [z[t - j] for j in range(1, L + 1)]
        if seasonal:
            row += [1.0 if months[t + 1] == m else 0.0 for m in range(2, 13)]
        rows.append(row)
        target.append(pi[t])
    names = ["constant", "headline rate, previous period"] + [
        f"driver{' change' if spec.method == 'pass_through' else ''}, lag {j}"
        for j in range(1, L + 1)]
    if seasonal:
        names += [f"month {m}" for m in range(2, 13)]
    return np.asarray(rows), np.asarray(target), names


def _regression_model(y: np.ndarray, x: np.ndarray, months: np.ndarray, spec: ForecastSpec,
                      seasonal: bool) -> tuple[_Model, RegressionFit]:
    import statsmodels.api as sm

    L = spec.lags

    def estimate(train_y: np.ndarray, train_x: np.ndarray) -> Any:
        X, target, _ = _regression_design(train_y, train_x, spec, seasonal,
                                          months[:len(train_y)])
        return sm.OLS(target, X).fit(cov_type="HAC", cov_kwds={"maxlags": max(1, L)})

    def simulate(res: Any, train_y: np.ndarray, train_x: np.ndarray, horizon: int,
                 level: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Recursive simulation with resampled residuals. Beyond the
        driver's last observation the driver is held at its last level (a
        zero change for pass-through): the assumption the result states."""
        rng = np.random.default_rng(spec.seed)
        beta, resid = np.asarray(res.params), np.asarray(res.resid)
        pi_hist = list(np.diff(train_y) * 100)
        z_hist = list((np.diff(np.log(train_x)) * 100) if spec.method == "pass_through"
                      else train_x[1:])
        hold = 0.0 if spec.method == "pass_through" else float(train_x[-1])
        n0 = len(train_y)
        paths = np.empty((spec.simulations, horizon))
        for s in range(spec.simulations):
            pi, z = list(pi_hist), list(z_hist)
            level_now = float(train_y[-1])
            for k in range(horizon):
                month = months[n0 + k] if n0 + k < len(months) else (
                    (months[n0 - 1] + k) % 12 + 1)
                row = [1.0, pi[-1]] + [z[-j] for j in range(1, L + 1)]
                if seasonal:
                    row += [1.0 if month == m else 0.0 for m in range(2, 13)]
                step = float(np.dot(beta, row)) + (float(rng.choice(resid)) if s else 0.0)
                pi.append(step)
                z.append(hold)
                level_now += step / 100
                paths[s, k] = level_now
        mean = paths[0]
        lower, upper = np.quantile(paths[1:], [(1 - level) / 2, (1 + level) / 2], axis=0)
        return mean, lower, upper

    def fit(train: np.ndarray, train_x: np.ndarray | None) -> Forecaster:
        assert train_x is not None
        res = estimate(train, train_x)
        return lambda horizon, level: simulate(res, train, train_x, horizon, level)

    def residuals(train: np.ndarray, train_x: np.ndarray | None) -> np.ndarray:
        assert train_x is not None
        return np.asarray(estimate(train, train_x).resid)

    full = estimate(y, x)
    _, _, names = _regression_design(y, x, spec, seasonal, months)
    table = pd.DataFrame({"term": names, "estimate": np.asarray(full.params),
                          "std_error": np.asarray(full.bse), "p_value": np.asarray(full.pvalues),
                          "reading": "correlational association, not a causal estimate"})
    rho = float(full.params[1])
    driver_sum = float(np.sum(full.params[2:2 + L]))
    long_run = driver_sum / (1 - rho) if abs(1 - rho) > 1e-9 else float("nan")
    regression = RegressionFit(coefficients=table, long_run=long_run,
                               r_squared=float(full.rsquared), observations=int(full.nobs))
    label = METHODS[spec.method]
    description = (f"{label}: monthly headline rate on its own lag and {L} lags of the "
                   f"driver{' change' if spec.method == 'pass_through' else ' level'}"
                   f"{', with month dummies' if seasonal else ''}; OLS with HAC errors")
    params = {n: float(v) for n, v in zip(names, full.params, strict=True)}
    return _Model(label, description, fit, residuals, params), regression


# ---------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------
def residual_diagnostics(resid: np.ndarray, fitted_params: int, period: int = 12
                         ) -> pd.DataFrame:
    """Ljung-Box (no remaining autocorrelation), Jarque-Bera (normal
    errors, which the model-implied interval assumes) and Engle's ARCH test
    (constant variance, which it also assumes)."""
    from statsmodels.stats.diagnostic import acorr_ljungbox, het_arch
    from statsmodels.stats.stattools import jarque_bera

    r = np.asarray(resid, dtype=float)
    r = r[np.isfinite(r)]
    rows = []
    lags = int(min(2 * period, max(4, len(r) // 5)))
    lb = acorr_ljungbox(r, lags=[lags], model_df=min(fitted_params, lags - 1))
    rows.append(("Ljung-Box, no autocorrelation left in the residuals",
                 float(lb["lb_stat"].iloc[0]), float(lb["lb_pvalue"].iloc[0])))
    jb, jb_p, _, _ = jarque_bera(r)
    rows.append(("Jarque-Bera, normally distributed residuals", float(jb), float(jb_p)))
    arch = _quiet(lambda: het_arch(r, nlags=min(12, max(1, len(r) // 10))))
    rows.append(("ARCH LM, constant residual variance", float(arch[0]), float(arch[1])))
    table = pd.DataFrame(rows, columns=["test", "statistic", "p_value"])
    table["verdict"] = np.where(table["p_value"] < 0.05, "rejected at 5%", "not rejected")
    return table


# ---------------------------------------------------------------------
# The forecast
# ---------------------------------------------------------------------
def _monthly(series: pd.Series) -> pd.Series:
    clean = series.dropna().astype(float)
    if clean.empty:
        raise ForecastError("the series is empty")
    if (clean <= 0).any():
        raise ForecastError("an index level must be positive to be modelled in logs")
    index = pd.DatetimeIndex(clean.index)
    expected = pd.date_range(index[0], index[-1], freq="MS")
    if len(expected) != len(index) or not (expected == index).all():
        raise ForecastError("the series must be monthly with no gaps")
    return pd.Series(clean.to_numpy(), index=expected, name=series.name)


def forecast(series: pd.Series, spec: ForecastSpec | None = None,
             driver: pd.Series | None = None) -> ForecastResult:
    """Forecast `series` (an index level, monthly) with `spec.method`, and
    backtest it and its benchmark on the same rolling origins."""
    from scipy.stats import norm

    spec = spec or ForecastSpec()
    if spec.method not in METHODS:
        raise ForecastError(f"unknown method {spec.method!r}; one of {sorted(METHODS)}")
    level_series = _monthly(series)
    y = np.log(level_series.to_numpy())
    n, H, R, m = len(y), spec.horizon, spec.origins, spec.seasonal_period
    first_origin = n - H - R + 1
    if first_origin < max(3 * m, 36):
        raise ForecastError(
            f"{n} periods are too few for {R} backtest origins at horizon {H}: the first origin "
            f"would have {first_origin} periods to fit on. Reduce the origins or the horizon.")
    seasonal = is_seasonal(pd.Series(y), m)
    months = np.asarray(pd.DatetimeIndex(level_series.index).month)

    x_aligned: np.ndarray | None = None
    regression: RegressionFit | None = None
    needs_driver = spec.method in ("pass_through", "phillips")
    if needs_driver and driver is None:
        raise ForecastError(f"the {METHODS[spec.method].lower()} needs a driver series")
    if driver is not None and spec.method in ("pass_through", "phillips", "sarimax"):
        aligned = pd.Series(driver).astype(float)
        aligned.index = pd.DatetimeIndex(aligned.index).to_period("M").to_timestamp()
        aligned = aligned.reindex(level_series.index)
        if aligned.isna().any():
            gaps = aligned.index[aligned.isna()]
            raise ForecastError(f"the driver has no value for {len(gaps)} of the headline's "
                                f"periods (first {gaps[0]:%b %Y}); it must cover them all")
        if spec.method == "pass_through" and (aligned <= 0).any():
            raise ForecastError("a pass-through driver is a price level and must be positive")
        x_aligned = aligned.to_numpy()

    def exog_for(train_len: int) -> np.ndarray | None:
        return None if x_aligned is None else x_aligned[:train_len]

    selection = y[:first_origin]
    if spec.method in ("arima", "sarimax"):
        use_seasonal = seasonal and spec.method == "sarimax"
        exog = x_aligned if spec.method == "sarimax" else None
        sel_exog = None if exog is None else exog[:first_origin]
        order, sorder, trend, _ = _sarimax_spec(selection, use_seasonal, m, spec.max_order,
                                                sel_exog)
        model = _sarimax_model(y, spec, use_seasonal, exog, order, sorder, trend)
        if spec.method == "arima" and seasonal:
            model = _Model(model.name, model.description + "; no seasonal terms, although the "
                           "series is seasonal", model.fit, model.residuals, model.params)
    elif spec.method == "ets":
        model = _ets_model(selection, y, spec, seasonal)
    else:
        assert x_aligned is not None
        model, regression = _regression_model(y, x_aligned, months, spec, seasonal)

    benchmark_name = "seasonal naive" if seasonal else "random walk"
    rows = []
    for origin in range(first_origin, n - H + 1):
        train = y[:origin]
        predictor = model.fit(train, exog_for(origin))
        mean, lower, upper = predictor(H, spec.level)
        bench = _benchmark(train, H, m, seasonal)
        for h in range(1, H + 1):
            actual = y[origin + h - 1]
            rows.append({
                "origin": level_series.index[origin - 1], "h": h,
                "target": level_series.index[origin + h - 1], "actual": actual,
                "method": mean[h - 1], "benchmark": bench[h - 1], "lower": lower[h - 1],
                "upper": upper[h - 1], "error_pct": (actual - mean[h - 1]) * 100,
                "benchmark_error_pct": (actual - bench[h - 1]) * 100,
                "inside": bool(lower[h - 1] <= actual <= upper[h - 1])})
    errors = pd.DataFrame(rows)
    backtest = Backtest(errors=errors, level=spec.level, method=model.name)
    per_origin = errors.groupby("origin")[["error_pct", "benchmark_error_pct"]].apply(
        lambda g: pd.Series({"m": float(np.mean(g["error_pct"] ** 2)),
                             "b": float(np.mean(g["benchmark_error_pct"] ** 2))}))
    stat, p_value = diebold_mariano(per_origin["m"].to_numpy(), per_origin["b"].to_numpy(), H)
    comparison = BenchmarkComparison(
        benchmark=benchmark_name, method=model.name,
        method_rmse_pct=backtest.rmse_pct,
        benchmark_rmse_pct=float(np.sqrt(np.mean(errors["benchmark_error_pct"] ** 2))),
        dm_statistic=stat, p_value=p_value, origins=backtest.origins, horizon=H,
        horizons_better=int((backtest.by_horizon["rmse_pct"]
                             < backtest.by_horizon["benchmark_rmse_pct"]).sum()))

    # The forecast itself, on all the data.
    predictor = model.fit(y, exog_for(n))
    mean, lower, upper = predictor(H, spec.level)
    future = pd.date_range(level_series.index[-1] + pd.offsets.MonthBegin(1), periods=H,
                           freq="MS")
    z = float(norm.ppf(0.5 + spec.level / 2))
    measured = backtest.by_horizon["rmse_pct"].to_numpy() / 100 * z
    path = pd.DataFrame({
        "point": np.exp(mean), "lower": np.exp(lower), "upper": np.exp(upper),
        "measured_lower": np.exp(mean - measured), "measured_upper": np.exp(mean + measured),
    }, index=future)
    path.index.name = "period"

    resid = model.residuals(y, exog_for(n))
    diagnostics = residual_diagnostics(resid, len(model.params), m)
    failed = diagnostics[diagnostics["p_value"] < 0.05]
    notes = [f"{test}: {verdict} (p = {float(p):.3f})" for test, verdict, p in
             zip(failed["test"], failed["verdict"], failed["p_value"], strict=True)]

    assumptions = [
        Assumption("Method", model.description,
                   "specification chosen on the data before the backtest window"
                   if spec.method in ("arima", "sarimax", "ets")
                   else "the analyst's choice on the Forecasts page"),
        Assumption("Estimation sample",
                   f"{level_series.index[0]:%b %Y} to {level_series.index[-1]:%b %Y} "
                   f"({n} periods)", "the compiled run's headline series"),
        Assumption("Transformation", "modelled in logs; the point path is the median",
                   "PriceLab forecasting convention (docs/methodology/forecasting.md)"),
        Assumption("Benchmark", f"{benchmark_name} (the series is "
                   f"{'seasonal' if seasonal else 'not seasonal'}: STL seasonal strength "
                   f"{seasonal_strength(pd.Series(y), m):.2f}, threshold "
                   f"{SEASONAL_STRENGTH_THRESHOLD})",
                   "Hyndman and Athanasopoulos, Forecasting: Principles and Practice"),
        Assumption("Interval", f"{spec.level:.0%} model-implied, assuming the model's error "
                   "distribution holds", "the fitted model"),
        Assumption("Backtest", f"{R} rolling origins, horizons 1-{H}, refitted at each origin",
                   "PriceLab forecasting convention"),
    ]
    if x_aligned is not None and driver is not None:
        name = spec.driver_name or str(getattr(driver, "name", "") or "")
        held = ("no change in the driver after its last observation" if spec.method ==
                "pass_through" else "the driver held at its last observed level")
        assumptions.append(Assumption("Driver", name, spec.driver_source))
        assumptions.append(Assumption("Driver path over the horizon", held,
                                      "PriceLab forecasting convention: no view is taken on "
                                      "the driver"))
    return ForecastResult(
        series=str(series.name or "headline"), method=spec.method, spec=spec,
        history=level_series, path=path, backtest=backtest, benchmark=comparison,
        assumptions=tuple(assumptions), specification=model.name, diagnostics=diagnostics,
        regression=regression, warnings=tuple(notes), parameters=dict(model.params))
