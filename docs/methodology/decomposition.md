# Decomposition, core measures and base effects (`engine/decomposition.py`)

What an analyst asks first is rarely "what is the index level". It is how
fast prices are rising, what is driving it, and whether the movement is broad
or narrow. This module answers those from a set of component indices and
their weights, and every answer can be checked.

## Rates of change

| Rate | Formula (p periods a year) |
|---|---|
| Period on period | I(t) / I(t−1) − 1 |
| Year on year | I(t) / I(t−p) − 1 |
| Annualised | (I(t) / I(t−1))^p − 1 |
| Three months on the previous three | Ī(t−2..t) / Ī(t−5..t−3) − 1, and annualised: that ratio^(p/3) − 1 |
| Cumulative | I(t) / I(base) − 1 |

For a quarterly series "three on three" is one quarter on the one before,
which is the same question. Every rate is in percent, and a period without a
comparison period is NaN, not dropped.

## Contributions at every level of the tree

The tree is rolled up by `engine/aggregation.aggregate_tree` — each parent
is the weighted arithmetic mean of its children and carries the sum of their
weights — and each parent's children are decomposed by
`engine/aggregation.contributions`:

    c_i→parent = w_i (I_i(end) − I_i(start)) / Σ_j w_j I_j(start) × 100

A contribution to the parent is rescaled to the headline by the parent's
share of the root's weighted level:

    c_i→headline = c_i→parent × W_p I_p(start) / (W_root I_root(start))

Because W_p I_p = Σ_i w_i I_i at every node, the contributions nest: a
division's groups sum to the division's contribution, the divisions sum to
the headline change, at every level at once. `tree_contributions` reports
the largest gap anywhere in the tree as `residual_pp`, and `reconciles` is
true when it is below half a unit in the eighth decimal place.

**On real data.** `tests/test_decomposition.py` pulls the euro-area HICP
through the Eurostat connector (replaying responses recorded from live calls):
55 series (all items, twelve divisions, their groups) and the matching item
weights. `data/connectors/eurostat.hicp_tree` builds the three-level tree the
way the HICP itself is compiled within a year: the year's item weights, which
Eurostat price-updates to December of the year before, applied to indices
rebased to that December. Rolling the 42 published groups back up reproduces
the published all-items index to within 0.004 index points in every month of
2025 — the rounding of one-decimal published figures — which is an independent
check that the tree is the HICP's own arithmetic. Contributions then reconcile
to eight decimal places at every level for every month; the measured residual
is of order 1e-14.

Two features of the real data are reported rather than smoothed over:

- **CP08** (communications) has one published group, CP081, carrying 1.20 of
  the division's 25.51 per mille. The division is used as a leaf in its own
  right, and the substitution is stated, rather than rebuilding it from a
  twentieth of its parts.
- **CP05**'s published groups sum to 61.01 per mille against a published
  division weight of 61.02. `aggregate_tree` reports the disagreement and
  uses the children's sum, because an aggregate that is not the sum of its
  parts cannot decompose into contributions that add up.

**Without weights** (a collection with no weight column) the compiled headline
is an equally weighted geometric mean, which has no exact additive
decomposition. The module says so and computes no contributions, rather than
decomposing an arithmetic aggregate under the geometric headline's name.

## Core and underlying measures

Every weighted measure uses **effective weights**, w_i × I_i(t−h) — the
weight a fixed-basket aggregate gives component i's change over the horizon
h. Under them the weighted mean of component changes *is* the aggregate's
change, so a zero-trim trimmed mean equals the headline exactly (tested on the
HICP). Over a year-on-year horizon in an annually re-weighted index one set of
weights spans a chain link and is an approximation; results that do so say
it.

| Measure | Parameters | Needs |
|---|---|---|
| Exclusion based | the excluded nodes (a parent excludes everything beneath it) | weights, and something left after the exclusions |
| Trimmed mean | the share of the basket trimmed from each tail, 0 ≤ α < 50% | weights, at least three components |
| Weighted median | — | weights, at least three components |
| Variance weighted | the volatility window | weights, and more history than the window |
| Sticky price | the duration threshold, 4.3 months by default | item-level prices in consecutive periods |

`core_measure_availability` returns, per measure, None or the reason it
cannot run, and the page prints both the requirement and the reason — the
contract `engine/index.formula_availability` gives the formula selector.
Published component indices carry no item-level prices, so on the HICP the
sticky-price measure is offered with the reason it cannot be computed.

**Trimmed mean.** Sort the period's component changes; normalise the
effective weights to sum to one; each component keeps the part of its weight
lying between cumulative weights α and 1 − α, so a component straddling a
trim point is kept in part (Kearns 1998; the Reserve Bank of Australia's
treatment). **Weighted median**: the change at which cumulative weight first
reaches one half; when it lands exactly on a boundary, the average of the two
neighbours. **Variance weighted**: effective weight divided by the variance of
the component's own changes over the `window` periods *before* the one
measured (trailing, so it is computable in real time and a shock does not
lower its own weight); a component with no variation over the window has its
volatility floored at the smallest non-zero one that period, and the floor is
reported. **Sticky price**: each component's frequency of price change is
measured from consecutive observations of the same item (a gap in an item's
record says nothing about whether its price changed); the implied duration is
its reciprocal in months, and components lasting longer than the threshold
form the sticky basket (Bryan and Meyer 2010, whose 4.3-month cut-off is the
median duration in the US CPI microdata).

## Base effects

Two exact splits of the year-on-year rate, with D the last period of the
previous calendar year:

    yoy(t)            = [I(D) − I(t−p)] / I(t−p)  +  [I(t) − I(D)] / I(t−p)
                      =      carry-over          +        impulse

    yoy(t) − yoy(t−1) = [I(t) − I(t−1)] / I(t−p)  −  I(t−1)[I(t−p) − I(t−p−1)] / [I(t−p) I(t−p−1)]
                      =   this period's movement  −        base effect

Carry-over is the rise already in the bag by December, before this year's
prices have moved; the impulse is this year's movement since. The base effect
is last year's same-period movement leaving the comparison. Both are
identities, not approximations: the tests check them to 1e-12 on the HICP
headline. A series with gaps is refused, because "a year ago" and "last
December" are positions in it.

## Diffusion and dispersion

**Diffusion**: the shares of components rising, unchanged (within a
tolerance, since a published index rounded to one decimal can show a movement
that is rounding) and falling; the diffusion index is the share rising plus
half the share unchanged, so 50 is balanced. With weights, the share of the
basket rising, and the share rising faster than a threshold.

**Dispersion**: the weighted standard deviation of component changes around
their weighted mean (relative price dispersion), and the weighted skewness.
A positive skew is a long upper tail: a few large rises pulling the mean above
the typical change, which is exactly when a trimmed mean and the headline
part company.

## Citation

CPI Manual 2020 (contributions to change and the presentation of core and
analytical series). Eurostat, *HICP Methodological Manual* (2018), on weights
price-updated to December and annual chain-linking. Bryan and Cecchetti,
"Measuring Core Inflation", in Mankiw (ed.), *Monetary Policy*, NBER (1994) —
trimmed means and the weighted median. Kearns, "The Distribution and
Measurement of Inflation", Reserve Bank of Australia Research Discussion
Paper (1998). Diewert,
"On the Stochastic Approach to Index Numbers" (1995) — variance weighting.
Bryan and Meyer, "Are Some Prices in the CPI More Forward Looking Than Others?
We Think So", Federal Reserve Bank of Cleveland Economic Commentary (2010).

## What the module does not do

Contributions are for fixed-weight (within-link) comparisons. Contributions
to a change spanning a chain link in an annually re-weighted index (the
"Ribe" decomposition) are not computed; a year-on-year contribution across
December uses one set of weights and is labelled an approximation. Core
measures are computed on the components supplied, at the level supplied: a
trimmed mean over 12 divisions and one over 90 classes are different
measures, and the level is the analyst's choice. No seasonal adjustment is
applied before the core measures; a period-on-period trimmed mean on
unadjusted data carries the season.
