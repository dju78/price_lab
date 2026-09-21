# Statistical disclosure control (`core/security.py`, `reporting/exports.py`)

## Rule

Every published table is built from `publication_table`, which applies:

1. **Primary suppression.** A cell built from fewer than
   `PRICELAB_SUPPRESSION_MIN_COUNT` matched quotes (default 3) is suppressed.
   The count is the number of items matched behind that period's comparison
   for that series, as published beside every level.
2. **Secondary (complementary) suppression.** The all-items aggregate is an
   equally weighted geometric mean of the category series, so a period that
   published its aggregate and every category but one would let a reader
   recover the suppressed one. In any period with exactly one primary
   suppression and a published aggregate, the smallest remaining published
   category cell is suppressed too.

A suppressed cell is written as the word **suppressed** with the rule that
suppressed it in the adjacent column (CSV, Excel), as `OBS_STATUS="C"` with
an `OBS_COMMENT` and no value (SDMX-ML), and as "suppressed" in the
bulletin's table — never as a blank, because a blank cannot be told from a
missing observation.

## Scope and limits

`suppress_with_secondary` certifies protection against recovery from a
*single* published total per group. Cells contributing to overlapping totals
(a category total and a regional total, say) need a cascading solver, and
the function refuses (`SuppressionCoverageError`) rather than run an
incomplete pass. The rules applied are stated on every export's provenance
stamp whether or not any cell in that export was suppressed.

## Citation

UN Fundamental Principles of Official Statistics (principle 6,
confidentiality); the suppression tests in Appendix 2 test 10 of the
platform specification.
