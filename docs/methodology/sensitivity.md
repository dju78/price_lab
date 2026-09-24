# Methodological sensitivity (`engine/sensitivity.py`)

## What it measures

How much the headline depends on choices of method: the run is recompiled
with one setting changed at a time, across six dimensions, and the spread is
reported as a range with the settings at each end named.

| Dimension | Alternatives |
|---|---|
| Elementary formula | every formula the data supports (Dutot, Carli, and the quantity-weighted formulae when there are quantities) |
| Aggregation formula | equally weighted arithmetic, geometric and harmonic means; expenditure-weighted forms when there are weights |
| Multilateral method and window | each method the data supports, at 13- and 25-period windows |
| Quality adjustment | linked without adjustment, and not linked, when the ledger values any replacement |
| Imputation method | each of the other imputation methods |
| Seasonal treatment | weight update instead of class confinement, when an item is strictly seasonal |

An alternative the data cannot support is listed with the reason, so the
range cannot look narrow merely because some choices were not tried. Two
that would only duplicate the run are excluded and say so: the Laspeyres,
which without weights falls back to the Jevons, and the equally weighted
geometric mean, which is the run's own aggregation when there are no weights.
The seasonal treatments are compiled on their own aggregator, so the
alternative is expressed as the ratio of the two applied to the published
headline.

## A lower bound

One choice is varied at a time. What two choices do together, such as a
different formula *and* a different imputation, is never computed. The
interactions between choices are therefore excluded, and the true
methodological range is wider. The range is a **lower bound** on the
methodological range, and it says so in those terms wherever it appears:
- its label (and so every headline caption that carries it);
- the chart title;
- the Uncertainty page.

## The imputed share of the aggregate

Imputation is the one dimension whose effect depends on how much of the
aggregate it touches. So every row of the table carries the imputed share of
the aggregate under that setting: the share of each category's expected
quotes that were filled rather than observed, weighted by the category's
weight (equal weights when there are none). The share is given for the final
period and averaged over the run. The result also carries the published
run's share, and the share neither observed nor imputed. The Uncertainty page
shows them beside the table.

## On the bundled collection

All items in December 2025 (January 2015 = 100) is published at 135.60.
Across 12 alternatives it runs from **118.12** (overall-mean imputation) to
**138.57** (the Carli formula), a spread of **20.45 points**, at least.
Imputation dominates: carrying prices forward or filling from the overall
mean moves the headline by about 17 points; the formula by about 3; the
aggregation by about 2; the multilateral window and the seasonal treatment by
under one. (Phase 9a's note said 13 alternatives; the count of those computed
is 12.)

That seventeen points needs its share beside it. The published run imputes
**nothing**, and 10% of the December 2025 aggregate is neither observed nor
imputed: Strawberries, out of season, is one of ten equally weighted
categories. Carrying prices forward, or filling from the overall mean, fills
exactly that 10% in December 2025, and 6.1% of the aggregate over the run,
5.3 points of it Strawberries (filled in 53% of its months). So the seventeen points
are not a modest fill moving the headline a long way. They are a decision to
price a strictly seasonal item through its months off the shelf, which the
seasonal methodology (seasonal.md) treats as a different question. Read the
imputation rows as that question, not as a choice among fills.

## Not a confidence interval

The range answers how much the number would move under a different
defensible choice on the same data. A confidence interval
([uncertainty.md](uncertainty.md)) answers how much it would move under a
different sample. They are different questions, and a reader who adds them,
or reads one as the other, understates one and overstates the other. So:

- `SensitivityResult.label` begins "Methodological sensitivity range — not a
  confidence interval" and ends by saying sampling uncertainty is reported
  separately and must not be added to it.
- The Uncertainty page shows them under two headings, in two charts, and
  offers no control that combines them.
- Every headline that carries both shows them as two separate statements.
- `reporting/charts.mark` tags each drawn spread with its kind, and
  `check_axes` refuses any axis carrying both; `tests/test_uncertainty.py`
  holds that.

## What the module does not do

It varies one choice at a time, not combinations, so interactions between
choices are not measured. It does not weight the alternatives by how
defensible each is: a range is a range, not a distribution.
