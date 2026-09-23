# Seasonal items and seasonal adjustment (`engine/seasonal.py`)

## Two problems, one word

**Strictly seasonal items** are off the shelf for part of every year. A
matched index simply drops them while they are gone, which quietly
redistributes their weight and scores their return as price change. The
question is not how to smooth anything; it is what the basket is supposed to
mean while part of it does not exist.

**Seasonal variation in prices collected all year** is the opposite problem:
the index genuinely rises every December, and a reader wants to know what
happened apart from that. This is seasonal adjustment.

The module keeps them apart because conflating them is how an index acquires
a movement nobody put there. An item missing for three months once is a
collection failure and belongs to `engine/imputation.py`; an item missing the
same three months every year is a season. `strictly_seasonal_items` enforces
the distinction — an item qualifies only when it has been observed over at
least `min_years` years, is absent from some calendar periods entirely, and
is in season in no more than `max_in_season_share` of them — and returns the
items that did not qualify with the count that disqualified them, so "no
seasonal items were found" is checkable rather than a bare assertion.

## Strictly seasonal item treatments

| | Class confinement | Weight update |
|---|---|---|
| The absent item's weight | stays inside its class, carried by whichever of the class's items are in season | leaves the basket for the months it is absent; everything else is renormalised |
| The basket's shape | constant all year | varies month by month with what is on sale |
| Assumes | the class's in-season items move the way the absent one would have | the absent item's expenditure simply did not happen |
| Fails when | the class is dominated by the seasonal item | households bought a substitute |

Both are compiled by `compare_treatments` and the gap between them is
reported in index points, per period and at the end. Neither is chosen
silently, because the choice is a judgement about what a basket means while
part of it is off the shelf.

Two implementation decisions make that comparison mean something.

**Both use the same aggregator** (`_chained_weighted_aggregate`), differing
only in the weights they hand it. If confinement aggregated levels and
weight update chained relatives, the gap would mostly measure the
aggregation form rather than the treatment.

**Weight update's weights are built by subtraction** from exactly the
constant weights confinement uses: only the weight of items whose calendar
period lies outside their own observed season is removed. Computing them
instead as "the weight of what has a price this month" would also subtract
items an enumerator missed and items not yet introduced, so the two
treatments would differ substantially on a collection with ordinary churn
and no seasonal item anywhere in it. `tests/test_seasonal.py` asserts the
control: strip the seasonal items and the two series are identical to
machine precision.

The aggregation chains a weighted mean of the categories' period-on-period
*relatives*:

    I(t) = I(t-1) × [ Σ_c w_c(t) I_c(t)/I_c(t-1) ] / Σ_c w_c(t)

rather than taking a weighted mean of levels. A weighted mean of levels
under weights that move changes when the weights change even if no price
does, which is an arithmetic error that reads as inflation. Weighting the
movements has the property that matters: a period in which nothing moved
leaves the level exactly where it was, whatever the weights did.

## The Rothwell index

    P(t) = base_value × Σ_{i ∈ S(t)} q_i0 p_it / Σ_{i ∈ S(t)} q_i0 p̄_i0

`S(t)` is the set of items priced in period t, `p̄_i0` is item i's average
price over the whole **base year**, and `q_i0` its base-year quantity (one,
equally, where the collection carries none).

The construction exists for its denominator. A seasonal item has no single
base-period price, so an ordinary fixed-base index has nothing to divide by;
averaging over the base year gives every item one number that exists.

It is not a pure price index. Because the item set changes with the season, a
movement mixes price change with the changing composition of the basket, so
`items_by_period` is returned beside the levels and the note says so.

Rothwell (1958), JASA 53; ONS *CPI Technical Manual*, seasonal items.

## Counter-seasonal estimation

An out-of-season price is estimated by moving the item's last observed price
by the price movement of the items that **are** in season in its category,
period by period across the gap. The alternative — holding the price flat —
says the item did not move while everything around it did, and delivers the
whole accumulated difference in one month when it returns.

This is `engine/imputation.py`'s class-mean rule applied specifically, and
only, to the off-season cells of strictly seasonal items. It is separated
because the two answer different questions: class mean fills a gap in a
collection; this constructs a price for a product that was not for sale. The
second is a stronger claim, so every estimate is labelled
`imputation == "counter_seasonal"` wherever it appears, and never presented
as an observation.

## Seasonal adjustment

X-13ARIMA-SEATS where the binary is available, STL otherwise. Two rules are
enforced rather than recommended.

**1. The engine that ran is named, in every output.** X-13ARIMA-SEATS is what
statistical offices use and what a reader assumes on seeing "seasonally
adjusted". STL is a robust loess decomposition — a good one, and not the same
thing: no trading-day or Easter regressors, no outlier model, no ARIMA
extension of the series ends, no X-11 quality diagnostics. Presenting an STL
result as *the* seasonal adjustment without saying so is a misrepresentation.
`SeasonalAdjustment.label` carries the sentence and every caller prints it.
The places it reaches are enumerated in code, in
`reporting/exports.SEASONAL_SURFACES`:

| Surface | Where the engine and the unadjusted series appear |
|---|---|
| page | `pages/seasonal.py`: the status line before the chart, the chart, its caption, the CSV download's first line |
| chart | `reporting/charts.seasonal_adjustment_chart`: title and legend name the engine; the note under the axes carries the full label; both series drawn |
| method note | `engine/seasonal.adjustment_note`, quoted by every report, deck and bulletin |
| Markdown report | the Seasonality method section and the Seasonal adjustment table |
| Word report | the same section and table, and the chart |
| deck | the "What produced these series" slide and the seasonal adjustment chart slide |
| Excel pack | the Seasonal adjustment sheet and the Series basis sheet |
| bulletin | the chart, the table, "How each series was produced", the methodology note |
| CSV | the `basis` column on the adjusted rows, and the unadjusted rows |
| SDMX-ML | a `BASIS` attribute on the adjusted series, and the unadjusted series |

`tests/test_seasonal_carried.py` renders every one of them and checks that
each names the engine, says when it is the fallback, carries the unadjusted
series, and states the direct-adjustment warning below. It also scans
`pages/` and `reporting/` for any module that handles the adjusted series and
fails if one is not in the table: a new output cannot ship without joining
the check. Writing that test found one gap — the SDMX message carried the
adjusted series under its key with no statement of what adjusted it — which
the `BASIS` attribute closes.

`adjustment_engine` is a request, not a guarantee. `"x13"` raises where the
binary is absent rather than substituting; `"auto"` falls back and records
why in `fallback_reason`, which then appears inside `label`; `"stl"` asks for
STL and has nothing to qualify.

Availability is decided by **looking for the executable**, not by importing
statsmodels' wrapper — the wrapper imports perfectly well on a machine with
no X-13 anywhere, and a check that passed on import would make the fallback
silent.

**2. The unadjusted series is published alongside the adjusted one,
everywhere.** `SeasonalAdjustment` holds both, so there is no code path on
which an output can have one without the other, and
`reporting/exports.additional_series` emits the pair from a single loop.

Seasonal factors are **normalised** so that they average to one over every
full year (`_normalise_factors` subtracts a centred moving average of one
year's width from the log-seasonal component). STL does not constrain its
seasonal component to be neutral over a cycle, so without this the component
carries a slow drift of its own and dividing it out moves the trend of the
adjusted series — precisely the thing adjustment must not do.

## Stability and the spurious-trend check

`stability` re-estimates the seasonal factors on sub-spans of the sample and
reports, per calendar period, how far apart the estimates were. A factor that
moves a great deal between halves of the sample is not a seasonal pattern; it
is a curve fitted to whatever each stretch happened to do, and an adjusted
series built on it will be revised when more data arrives.

It also reports `trend_difference_pp`: the gap between the adjusted and
unadjusted series' annualised trends, fitted by least squares on the log
levels rather than read off the endpoints (endpoints on a seasonal series
measure the two months it started and ended in). Seasonal factors that
average out over a year cannot change a trend, so any material value here is
the adjustment manufacturing or destroying growth, and it is reported whether
or not it is large.

## A series with no seasonality

The companion check to the spurious-trend test: adjusting a series that has
no seasonal pattern must return it essentially unchanged. Two tolerances,
because there are two cases.

**No noise.** A pure trend is returned identical to floating-point
precision: the test bound is 1e-10 in log terms, and the measured change is
1.7e-14. Anything above rounding would be a seasonal pattern invented from a
straight line.

**Noise.** A noisy series cannot come back *exactly* unchanged. STL
estimates each calendar month's factor from that month's few observations,
so the estimate carries sampling error of roughly σ/√(observations per
month), and dividing it out moves each point by a fraction of the noise.
The right yardstick for "essentially unchanged" is therefore the irregular
component itself, not a fixed percentage — a fixed 0.1% would be failed by
a correct adjustment of a noisy series and passed by a broken one on a
smooth series. The test: the root-mean-square change, in logs, is at most σ,
and the trend moves by less than 0.1 points a year. Over 40 seeds of ten
years of monthly data the measured change was a median 0.62σ and at worst
0.77σ; the test runs ten of those seeds. On four years of data the worst of
40 seeds reached 1.13σ: short samples fit larger spurious factors, which is
one more reason the stability test reports its sub-sample count.

The same test is shown to discriminate: a genuine 5% seasonal wave, adjusted,
changes the series by more than 5σ.

## Direct adjustment, and the parts that need not add up

Every adjustment here is **direct**: the named series itself is adjusted,
not assembled from separately adjusted components (indirect adjustment). The
two give different answers and neither is wrong, but they do not agree: the
seasonally adjusted components need not sum to the seasonally adjusted
total, and `tests/test_seasonal_carried.py` shows the gap on the bundled
collection. Offices that publish both either constrain the components to the
total (benchmarking) or publish the gap. This platform does neither, so it
says so: `SeasonalAdjustment.additivity_note` is part of `label`, and so
appears on every surface in the table above.

## The seasonal multilateral forms

The year-over-year monthly index and the rolling year index live in
`engine/multilateral.seasonal_multilateral` — each calendar month gets its
own multilateral index over its own observations across the years, so
seasonality never enters the comparison at all. See
[multilateral.md](multilateral.md).

## Citation

CPI Manual 2020, Chapter 11 (seasonal products; strictly seasonal items and
the treatment options, paras 11.10–11.18; year-over-year and rolling year
indices, paras 11.19–11.55) and Chapter 14 (seasonal adjustment). Rothwell
(1958). X-13ARIMA-SEATS: US Census Bureau. STL: Cleveland, Cleveland, McRae
and Terpenning (1990).

## What the engine does not do

No X-11 or SEATS quality statistics (M and Q diagnostics, sliding-spans,
revision history) are computed when STL runs — they are X-13's and are not
reimplemented. There is no trading-day, moving-holiday or leap-year
adjustment on the STL path. Seasonal adjustment is applied to one series at a
time and is not made additively consistent across an aggregation structure:
adjusting the components and adjusting the total give different answers, and
this module does not reconcile them — it labels the gap instead (above). There
is no test for identifiable seasonality before adjusting (X-13's combined
test, or QS): a series with none is adjusted anyway, and the null test above
bounds what that does to it.
