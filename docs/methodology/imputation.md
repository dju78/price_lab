# Imputation of missing prices (`engine/imputation.py`)

Every imputed value stays flagged (`imputation` column names the method) and
is reported as a share of the aggregate it feeds (`response_rates`), because
imputation is designed to be invisible in the output and that is exactly why
it has to be counted.

| Method | What it does | Citation | Assumption / bias |
|---|---|---|---|
| `none` | Leaves the gap; the matched model skips the item for that comparison | Ch. 6, "overall mean imputation is numerically equivalent to omitting the item" | The item moved like the rest of the cell |
| `carry_forward` | Repeats the last observed price | Ch. 6, "carry forward" | Zero price change for the gap: biases the index towards zero change; the manual advises against it beyond very short gaps, and the engine warns |
| `class_mean` | Moves the item by the geometric mean change of its category peers, period by period, so a fill can carry forward through a multi-period gap | Ch. 6, "class mean imputation" (as an imputation of a temporarily missing price) | Cascades: an imputed value becomes input to the next fill; bridges long gaps at the cost of imputations compounding |
| `targeted_mean` | Anchors to the item's last *observed* price and carries it along the matched level of its own cell | Ch. 6, "targeted mean imputation" | Never cascades; leaves a gap unfilled when the cell has no matched pair, which shows in the response rate |
| `overall_mean` | As `targeted_mean` with the whole collection as the cell | Ch. 6, "overall mean imputation" | Strongest assumption: the item moved like everything; the fallback when a category is empty |
| `seasonal_hold` | Holds the level across an out-of-season gap | Ch. 11 (seasonal products), the "carry forward" treatment of out-of-season items | Reports no change out of season; the matched count is zero, and disclosure control suppresses the cell |

The golden examples in `tests/test_quality_adjustment.py` reproduce the
manual's Chapter 6 arithmetic for targeted and overall mean imputation
(outlet F's 5.99 → 6.26 → 6.32 → 6.34; equation 6.4's 1.023765).

## Mechanism first

`engine/quality.py` classifies each category's gaps by mechanism (seasonal,
collection, sporadic) before a method is chosen, and `auto_configure`
proposes a method per category from that. The proposal is shown on Ingest,
overridable per category, and the override is audited.

## Response rates

`response_rates` gives observed, imputed and still-missing counts per
category and period; `imputation_summary` gives one row per method used.
Both are on the Imputation page and in the evidence pack.
