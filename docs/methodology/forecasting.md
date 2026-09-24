# Forecasting (`engine/forecasting.py`, `engine/projection.py`)

## What a forecast here is

A statement about a model: what the headline would do if the model that fits
its past holds. A reader will treat a forecast as a fact about the future,
so a forecast never appears alone. Wherever it is shown or exported it carries
four things (`engine/projection.py`):

1. **its interval** — what the model says its own uncertainty is;
2. **its backtest performance** — the error the same method actually made
   forecasting periods already observed, from rolling origins;
3. **its benchmark comparison** — the same backtest for a naive benchmark on
   the same origins and horizons, and whether the method beats it;
4. **its assumptions** — every one, with its value and its source.

`reporting/projections.release` refuses to export a forecast that lacks any
of them.

## Methods

| Method | Specification |
|---|---|
| ARIMA | ARIMA(p,d,q) on the log level; d by repeated KPSS tests at 5%; p, q ≤ 2 by AICc; a drift after one difference |
| SARIMAX | seasonal ARIMA, (P,D,Q)[12] with D = 1 and P, Q ≤ 1 when the series is seasonal, and an optional external regressor |
| ETS | additive-error exponential smoothing on the log level: trend none, additive or damped; additive seasonality when seasonal; by AICc |
| Pass-through regression | π<sub>t</sub> = a + ρ π<sub>t−1</sub> + Σ<sub>j=1..L</sub> b<sub>j</sub> Δx<sub>t−j</sub> + month dummies + e, where π is the headline's monthly log change and Δx the driver's, both × 100; OLS with HAC errors |
| Phillips-curve regression | the same, with the driver's level (an unemployment rate, an output gap) in place of its change |

A series is **seasonal** when its STL seasonal strength exceeds 0.64, the
threshold `nsdiffs` uses (Hyndman and Athanasopoulos). The ARIMA method
never adds seasonal terms, and on a seasonal series it says so.

The specification (order, or ETS form) is chosen on the data **before** the
backtest window opens and then held fixed. The backtest is therefore out of
sample in its specification as well as its parameters; the final forecast
refits that specification on all the data.

## The benchmark rule

The benchmark is the **random walk** (the last value) for a level, and the
**seasonal naive** (the value a whole number of years back) for a seasonal
series. At each rolling origin both the method and the benchmark forecast
horizons 1 to H, and both are scored on the same outcomes.

A method **beats** the benchmark only when its RMSE is lower *and* a
one-sided Diebold-Mariano test rejects equal accuracy at 5%. The test uses
each origin's mean squared error over the horizons, a Newey-West variance at
H − 1 lags (consecutive origins' multi-step errors overlap) and the
Harvey-Leybourne-Newbold small-sample correction. A lower RMSE that the test
cannot tell from chance is reported as not beating the benchmark, with both
numbers. The statement also says at how many horizons the method's RMSE is
the lower.

The verdict is the first thing the label says after the number, and the
chart title repeats it. A model that loses to the random walk says so above
its own forecast.

On a random walk, `tests/test_forecasting.py` holds that the fitted model
does not beat the benchmark and that the label says so. Across ten random
walks it beats the benchmark at most twice (by luck, about one time in
twenty). On the bundled series a seasonal model does beat it: the check is
live, not decorative.

## Interval against measured error

At each horizon the backtest sets the interval the model claimed (its mean
half-width across origins) against the half-width its measured errors imply
at the same level (z × backtest RMSE). Where the measured one is wider, the
label says the model is **understating its own uncertainty**. It names at how
many horizons, and the worst ratio. The path carries both intervals:
`lower`/`upper` from the model, and `measured_lower`/`measured_upper` from the
backtest errors.

A regression's interval holds the driver at its last observed value (no
change, for pass-through) and resamples the regression's residuals. It
therefore leaves out the driver's own uncertainty, and its backtest shows the
gap. In the test's simulated pass-through, the model-implied 95% interval
contains the outcome in about 69% of backtest forecasts.

## Regressions are correlational

A pass-through or Phillips-curve coefficient describes how the headline and
the driver have moved together in the sample. It is not an estimate of what a
change in the driver would do to prices:
- the driver may respond to the same shocks as the headline;
- the lags may pick up expectations;
- the specification omits everything else.

Every place a coefficient appears carries `CORRELATIONAL`, which says so:
- the forecast's label;
- every row of the coefficient table (`reading`);
- the Forecasts page, above and below the table.

`tests/test_forecasting.py` scans the forecasting and scenario code and pages
for causal wording and fails on any.

## Diagnostics

The residual diagnostics are:
- Ljung-Box, for autocorrelation left in the residuals;
- Jarque-Bera, for normality;
- Engle's ARCH LM test, for constant variance.

The model-implied interval assumes the last two hold. A rejection is printed
beside the forecast, not left in the table.

## On the bundled collection

All items, Jan 2015 to Dec 2025, forecast to Dec 2026: 24 rolling origins at
horizons 1–12, against the seasonal naive. The series is strongly seasonal
(strength 0.99).

| Method | Specification | Backtest RMSE | Seasonal naive | Beats it | Implied interval too narrow at |
|---|---|---|---|---|---|
| ARIMA | ARIMA(2,1,1) with drift | 1.66% | 2.72% | yes, 39% lower | 1 of 12 horizons (1.01×) |
| SARIMAX | SARIMA(1,0,0)(0,1,1)[12] with drift | 0.32% | 2.72% | yes, 88% lower | 10 of 12 (up to 1.20×) |
| ETS | ETS(A,A,A) | 0.23% | 2.72% | yes, 91% lower | 2 of 12 (up to 1.03×) |

The margin says as much about the benchmark as about the models. The
collection's headline is a steady trend under a very regular seasonal
pattern. The seasonal naive repeats last year and so misses a year's trend,
about 2.7%, at every horizon, while the models carry the trend forward. The
ARIMA method, with no seasonal terms, leaves the seasonality in its residuals
(Ljung-Box rejects) and claims an interval wider than its errors. The
seasonal models' errors are close to their intervals, but SARIMA's are wider
at most horizons, and the label says so.

## What the module does not do

It does not:
- combine forecasts;
- pool models across series;
- include the driver's own uncertainty in a regression's interval (the
  backtest measures what that omission costs);
- use a Box-Cox transformation other than the log;
- re-select the specification at every backtest origin;
- forecast any series but a monthly one.

## Citation

Box, Jenkins, Reinsel and Ljung, *Time Series Analysis* (5th ed., 2015).
Hyndman and Athanasopoulos, *Forecasting: Principles and Practice* (3rd ed.,
2021), on the benchmark methods, seasonal strength and time series
cross-validation. Diebold and Mariano, "Comparing Predictive Accuracy",
*Journal of Business and Economic Statistics* (1995); Harvey, Leybourne and
Newbold, "Testing the equality of prediction mean squared errors",
*International Journal of Forecasting* (1997).
