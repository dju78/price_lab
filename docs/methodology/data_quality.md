# Value-level data quality (`engine/quality.py`, `data/validation.py`)

Not to be confused with quality *adjustment* (the treatment of item
replacement, [quality_adjustment.md](quality_adjustment.md)). This module is
about whether a recorded price is a price.

## Two principles

1. Diagnose the mechanism before choosing the treatment. A zero that means
   "out of season" and a zero that means "the collector could not visit" are
   different problems and do not share a rule.
2. Never modify silently. Every altered observation carries a flag naming
   what was done and why.

## Steps

- **Sentinel recoding.** Configured missing codes (default `0`) become
  unavailable, not a price of nil.
- **Mechanism classification.** Each category's gaps are classified as
  seasonal (regular, calendar-aligned), collection (whole periods missing) or
  sporadic, from the gap pattern; a proposal for a human, not a determination.
- **Unit-of-measurement fault detection.** Each observation is compared with
  a centred rolling median of its own item (default 13 periods, minimum 3): a
  deviation between `10^1.5` and `10^2.5` is an order-of-100 fault (pence for
  pounds, grams for kilos). A band rather than a threshold, because a unit
  fault has a known multiplier and genuine volatility does not cluster at one.
  Centred and local, so a trending series is not flagged for trending.
- **Repair.** A flagged observation is rescaled onto its item's level
  (`scale_error_x100` / `scale_error_div100`) rather than deleted, because
  deletion breaks the item continuity a matched comparison depends on; the
  choice to drop instead is a configuration switch. A post-repair residual
  check reports observations still beyond tolerance.

## Validation dimensions (`data/validation.py`)

Completeness, validity, consistency, uniqueness, timeliness, coverage,
conformity and plausibility, following the IMF Data Quality Assessment
Framework's dimensions; a critical finding blocks compiling until an analyst
accepts, excludes, corrects or justifies it, with the decision persisted and
audited. Characteristics files for the hedonic module pass through the same
engine (`assess_characteristics`): uniqueness of `item_id` is critical.

## Citation

CPI Manual 2020, Chapter 6 (data validation and editing), Chapter 11
(seasonal products); IMF DQAF for the dimensions.

## Known limits

The detector finds order-of-100 faults only; a factor-of-10 error or a
transposition is left to the plausibility findings. Mechanism classification
is heuristic and shown for confirmation.
