# Deflation, real values and PPPs (`engine/deflation.py`)

## What the module computes

A real value is a nominal value divided by a price index relative to its
level in a reference period:

    real(t) = nominal(t) / ( D(t) / D(ref) )

expressed "in constant ref prices". The reference is a period the deflator
observes, or a whole year, in which case D(ref) is the deflator's average
over that year and every period of the year must be present.

| Function | What it is |
|---|---|
| `deflate` | the general case |
| `real_wage`, `real_income` | a wage or an income deflated by consumer prices: purchasing power in the reference period's prices |
| `real_growth` | nominal, price and real growth over a horizon: (1 + real) = (1 + nominal) / (1 + inflation), exactly, with the familiar "nominal minus inflation" beside it and its error |
| `constant_prices`, `volume_index` | a current-price value series revalued at the reference period's prices, and that as an index (reference = 100): movement in quantity with price movement removed |
| `ppp_convert` | local-currency values divided by purchasing power parities (local currency per international dollar) |
| `price_level_index` | PPP over the market exchange rate × 100: above 100 more expensive than the reference, below it cheaper |

## Every result names its deflator and its reference period

`DeflationResult.label` reads, for example, *"average weekly earnings, real
wage in constant Jan 2024 prices: deflated by CPI (Jan 2024 = 100), aligned
monthly"*. The page prints it before the chart, the chart carries it as a
note, and the CSV download carries it on its first line. The table's columns
are named for it too: `deflator (Jan 2024 = 100)`, `real (constant Jan 2024
prices)`. A real number without its deflator and reference period is not a
real number anyone can use.

## Alignment is explicit

Frequency is read from the spacing of the periods, not from a declared
frequency, because the mistake guarded against is a series that says monthly
and holds one observation a quarter. An irregularly spaced series has no
frequency and is refused.

A monthly nominal series and a quarterly deflator **raise**, naming both
frequencies. They are never resampled silently: the user converts one
explicitly with `to_frequency` — by its mean (a price index, a rate), its sum
(a flow such as monthly earnings to a quarter) or its last value (a stock) —
and the conversion is recorded in the result's notes. `to_frequency` keeps
only complete periods (a quarter with two months is not a quarter) and refuses
to go to a *higher* frequency, which would invent the periods in between.

Nominal periods the deflator does not cover also raise: a deflator is not
extrapolated. `allow_partial=True` deflates only the periods both cover and
records the ones left out.

PPPs are annual. Converting a monthly or quarterly series with them means
holding each year's PPP constant through the year; that is refused unless
`broadcast_annual=True`, and when chosen it is stated in the label.

## Nominal and real on a chart

`reporting/charts.deflation_chart` draws nominal and real on one axis, which
the chart rule (`charts.check_figure`) allows only because both are in the
same currency, each line says which it is ("nominal (current prices)", "real
(constant Jan 2024 prices)"), and the axis label says what each is measured
in. The same rule refuses an index level, a percentage change and a
percentage-point contribution on one axis; see
[decomposition.md](decomposition.md) and `tests/test_decomposition.py`.

## Citation

System of National Accounts 2008, Chapter 15 (price and volume measures;
deflation, constant prices and volume indices). Eurostat-OECD *Methodological
Manual on Purchasing Power Parities* (2012) for PPPs and price level indices.
CPI Manual 2020 on the use of the CPI as a deflator of household income and
consumption.

## What the module does not do

Constant-price series are fixed-base: one reference period throughout.
Chain-linked volume measures, which re-weight every year and so are not
additive across components, are not produced; nor is double deflation of
value added. A PPP is applied to a single aggregate series; converting a
basket component by component with component-level PPPs is not done here.
