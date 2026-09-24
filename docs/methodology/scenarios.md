# Scenarios (`engine/scenarios.py`)

## A scenario is not a forecast

A scenario says what the headline would do *if* stated shocks occurred and
passed through at stated rates. It says nothing about whether they will. It
is therefore never labelled, charted or exported as a forecast:
- its label begins "Scenario -- not a forecast";
- its chart is titled "Scenario (not a forecast)";
- every column of its exports begins "scenario: ".

`tests/test_forecasting.py` fails if a scenario export mentions a forecast
other than to say it is not one.

## The path

**Baseline rule.** The headline repeats its last twelve months' seasonal
pattern and grows at its last twelve months' rate. For a series with no
seasonality, it grows at that rate evenly. The rule is stated, not
estimated, and is itself one of the assumptions.

**Shocks.** A shock is a permanent change in one driver's level:
- energy prices;
- the exchange rate (a rise is a depreciation);
- wages;
- administered prices.

Each shock has a size in per cent, the projected month it begins, and the
number of months over which it passes through (linearly). It passes into the
headline level with an elasticity, the **coefficient**, which must carry its
**source**. The scenario path is the baseline plus each shock's contribution,
in logs, and every shock's contribution is a column of the path.

**Where a coefficient comes from.**
- **Stated by the author**, with the source written beside it (a published
  study, an official model's multiplier).
- **The run's own weights.** For a driver whose prices are categories of the
  index itself, the direct first-round effect is the categories' share of the
  aggregate. The source says so, and that second-round effects are excluded.
- **A pass-through regression** on the Forecasts page. Its long-run sum is
  used, and the source says it is correlational, not causal.

## The fan

The baseline rule is run from rolling origins (36 by default) over periods
already observed. The fan at each horizon is made of the quantiles of its
errors there: 50%, 80% and 95%. It measures how far the headline has strayed
from the rule before. It does not include uncertainty about the shocks or
their coefficients, and the label says so.

Because the fan is built from those errors, it matches them by construction.
The backtest statement says so instead of claiming a calibration check. The
rule is also scored against the naive benchmark on the same origins, so a
reader knows whether the baseline itself beats no change.

## The assumption list is part of the output

Every scenario lists:
- the baseline rule;
- the starting point;
- how the fan was built;
- how shocks pass through;
- each shock's size, timing and coefficient with its source.

The list sits directly under the chart, not in a footnote. It is the first
thing after the path in every export.

An assumption with no value or no source is **unstated**. A scenario carrying
one can be built and looked at, and its label and the page both say it cannot
be exported. `reporting/projections.release` refuses it on every export path.

## On the bundled collection

All items, Dec 2025 = 135.60, 24 months ahead. The baseline rule's backtest
RMSE is 0.55% (36 origins, horizons 1–24), against 4.23% for the seasonal
naive. It beats it at every horizon, because it carries the trend the naive
benchmark misses.

With energy prices +20% from month 3, passing through over 6 months at an
elasticity of 0.08, and wages +5% from month 1 at 0.2, All items would reach
146.65 in Dec 2027, against 143.12 under the baseline rule. The 95% fan runs
from 144.91 to 148.89.

## What the module does not do

It does not:
- estimate second-round effects;
- model interactions between shocks (they add in logs);
- put uncertainty on a coefficient into the fan;
- apply temporary shocks;
- produce anything a reader could take as a probability that a scenario
  occurs.
