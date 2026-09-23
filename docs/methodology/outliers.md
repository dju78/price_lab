# Outlier screening and the review queue (`engine/outliers.py`)

## What the module computes

Four screens over period-on-period **price relatives** — not price levels, a
level is not comparable between items and a movement is — producing a queue
of quotes for a person to decide about.

| Screen | Statistic | Fence |
|---|---|---|
| Tukey fences | log price relative | `Q1 − k·IQR` to `Q3 + k·IQR`, k = 1.5 |
| Quartile method | log price relative | `median − r·(median−Q1)` to `median + r·(Q3−median)`, r = 2.5 |
| Hidiroglou-Berthelot | `E_i = \|s_i\| · max(p_it, p_i,t−1)^u` | `E_med ± c·d`, with `d` floored at 5% of `E_med`, c = 4 |
| Period-on-period ratio | the relative itself | a fixed band, 0.5 to 2.0 |

where HB's centred score is

    s_i = 1 − median(r)/r_i   if r_i < median(r),   else   r_i/median(r) − 1

All four are run by default because they **disagree**, and a quote caught by
one is different evidence from one caught by all four. `OutlierScan.queue`
collapses the flags to one row per quote with the screens that caught it and
a count, so a reviewer can sort by agreement.

Tukey works in logs so that a halving and a doubling are the same size of
surprise; a fence drawn on raw ratios sits at different multiples above and
below one and gives the screen a direction nobody chose. The quartile method
scales each side by its own half of the distribution, so a cell skewed by a
promotion is not cut off merely for being long on one side. HB multiplies by
the magnitude of the price, so where a handful of quotes carry most of an
aggregate a large item is protected that a purely distributional screen would
miss; at `u = 0` it reduces to a screen on the ratio alone. The ratio screen
needs no distribution at all, which is why it exists: below
`min_cell_size` price relatives a cell has no usable quartiles, and that is
exactly where a doubled price would otherwise pass unnoticed.

## The deadband

`min_change_pct` (5% by default) is a tolerance: a quote whose relative sits
within that distance of its cell's median relative is never flagged,
whatever the fences say. Without one, a category whose relatives are tightly
clustered has fences a fraction of a percent wide and every ordinary rounding
becomes a queue entry — on the bundled collection, screening with no deadband
flags 23% of all price relatives, which is not a review queue, it is the
data. With it, 1.8%. Every statistical office applies a tolerance of this
kind for the same reason; it is a parameter rather than a constant because it
decides how much work the queue creates, which is an operational judgement.

## No quote leaves an index without a name against it

This is the module's whole design, not a policy layered on top of it:

- **Detection changes nothing.** `detect` returns flags. There is no
  argument to any function here that removes a quote.
- **An unreviewed flag excludes nothing.** `apply_decisions` with an empty
  ledger is a no-op.
- **A decision requires a reason**, enforced at four layers: the widget on
  the page, `core.config.OutlierDecision`'s validator,
  `core.ledger.record_outlier_decision`, and the `NOT NULL` column in
  migration 0007.
- **A rejected quote is marked, not dropped.** The row stays, gains
  `outlier_excluded`, `outlier_reason`, `outlier_decision` and
  `outlier_analyst`, and has its price set to unavailable so the index cannot
  use it. After a review the collection still contains every quote ever
  collected, and every one taken out of the index says who took it out and
  why.
- **Accepted and annotated quotes are marked too.** A quote somebody looked
  at and kept is evidence about the collection, and the record that a screen
  firing did not automatically become an exclusion.
- **Every decision reaches the audit log** (`core.audit.OUTLIER_DECISION`)
  with the analyst, the reason, the screens that flagged it and the ratio.
- **A decision is never deleted.** Re-deciding a quote withdraws the previous
  record with a reason rather than overwriting it, so the trail shows that
  somebody decided one thing and then decided another.

Decisions are keyed by the input data's **content hash**, like the quality
adjustment ledger and validation overrides, so they come back when the same
collection is uploaded again.

## Where it sits in the pipeline

Between the quality adjustment ledger and imputation. An excluded quote
leaves a hole, and the hole is filled by whatever imputation the run
configured rather than left for the matched index to trip over.

## Reporting the exclusions

As a count and as a **share of the quotes they would have fed** — the
denominator is the collection, not the number of decisions taken, because a
reviewer who rejected one of one flag has excluded one quote and not the
whole index. `by_category` and `by_period` carry the same shape
`engine/imputation.response_rates` reports imputation in, so a reader
comparing the two does not have to translate. A reader who can see the
imputation rate but not the exclusion rate has been shown half the treatment.

## Citation

Hidiroglou and Berthelot (1986), "Statistical editing and imputation for
periodic business surveys", *Survey Methodology* 12, 73–83. Tukey (1977),
*Exploratory Data Analysis*. Eurostat, HICP methodological manual, on price
relative screening and editing tolerances.

## Assumptions and known biases

Every distributional screen assumes the cell's relatives are a sample from
one distribution, which a category mixing genuinely different products
violates — the cell is the unit of analysis and a badly drawn cell produces
badly drawn fences. The screens look at movements only: a price that has been
wrong at the same wrong level for a year moves by nothing and is invisible
here (`engine/quality.py`'s scale-error detection, against an item's own
rolling median, is what covers that). HB's importance weighting is the right
trade where a few quotes dominate an aggregate and the wrong one where every
quote matters equally, which is why `u` is a parameter and not a default
nobody sees.

## What the engine does not do

No automatic exclusion, by design. No imputation of a rejected quote's value
beyond what the run's own imputation stage does. No item-level or
enumerator-level scoring across periods (a "this shop is always wrong"
signal), no selective editing score that ranks flags by their effect on the
aggregate rather than by how many screens agreed, and no macro-editing pass
over the compiled aggregates.
