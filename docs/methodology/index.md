# Matched-model elementary index (`engine/index.py`)

## What it computes

For each category, an index over time from the prices of items priced in
*both* of the two periods being compared (the matched model). Two
compilation methods:

- **Chained**: the level at period *t* is the level at *t−1* multiplied by the
  period-on-period relative computed over items matched between *t−1* and *t*.
  `I_t = I_{t-1} × P(p_{t-1}, p_t)`.
- **Fixed base**: every period is compared directly with the price reference
  period 0. `I_t = 100 × P(p_0, p_t)`, and the price reference period itself is
  100 by definition.

`P` is the elementary formula chosen for the run (Jevons by default; see
[elementary.md](elementary.md)). Periods with fewer than `min_matched_items`
matched items hold the previous level (chained) or carry no level (fixed
base), and are flagged `insufficient_match`.

## The three reference periods

The configuration distinguishes what a single "base period" conflates:

| Field | Meaning | Read by |
|---|---|---|
| `price_reference_period` | Denominator of every price relative; the index is 100 here by construction | fixed-base compilation |
| `weight_reference_period` | Period the expenditure weights or quantities are drawn from; deliberately earlier for Lowe and Young | `engine/bilateral.py` |
| `index_reference_period` | Period the *published* series is rebased to read `base_value` at | the final rebasing step, both methods |

Rebasing is a presentational rescaling applied to the finished series,
identically for chained and fixed-base runs; it changes every level and no
ratio between two periods. An `index_reference_period` absent from the data
is refused, and a period at which the series has no computable level makes
every level NaN rather than leaving the series un-rebased under a label that
claims otherwise (CPI Manual 2020, Chapter 9, "Rebasing").

## Citation

CPI Manual 2020, Chapter 8 (elementary indices; matched-model principle,
paragraphs 8.5–8.12), Chapter 9 (chaining and rebasing, paragraphs 9.25–9.36
and 9.101 onward).

## Assumptions

- An item identifier denotes the same product specification throughout its
  life. Where it does not, the replacement is a quality adjustment problem
  (see [quality_adjustment.md](quality_adjustment.md)).
- The entry or exit of an item is not price change. The matched model skips
  it; that is itself an implicit quality adjustment (it attributes the whole
  gap between a departing item and its successor to quality) and is stated as
  such in every method note.

## Known biases

- **Chain drift**: a chained index built on a non-transitive elementary formula
  (Carli) accumulates bias with every link; Jevons and Dutot chain to their
  direct counterparts exactly. Reported by `engine/diagnostics.py`.
- **Held levels**: a period that holds its level for want of matched items
  reports no price change where there may have been some. The matched count
  is published beside every level for this reason, and cells below the
  disclosure-control threshold are suppressed rather than printed.
