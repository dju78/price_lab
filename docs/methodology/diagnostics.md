# Diagnostics (`engine/diagnostics.py`)

Each diagnostic turns a methodological choice into a quantified consequence
on the run's own data, so the choice can be argued with.

| Diagnostic | What it reports | Why |
|---|---|---|
| Method sensitivity | The final level of every series under each elementary formula (and the custom one, when used), with the spread | The formula is a choice with a direction of bias ([elementary.md](elementary.md)); the spread is the size of that choice on this collection |
| Chain drift | Chained level against the direct fixed-base level at the final period, per series | A large gap on a seasonal or volatile series is the classic sign that chaining is accumulating drift rather than measuring price change ([splicing.md](splicing.md)) |
| Unmatched comparison | The index under the matched model against a naive ratio of mean prices that includes entering and leaving items | Quantifies how much the matched model's exclusion of replacement (its implicit quality adjustment) changed the answer |
| Seasonality | Regular calendar patterns in each series | Distinguishes a seasonal movement from a trend before either is interpreted |
| Churn | Item lifespans, entrants after the start, and the price level at which replacements enter relative to the items already there | Replacements entering systematically above the incumbents is the signature of the run-out/launch-price problem quality adjustment exists for |

## Citation

CPI Manual 2020, Chapter 8 (choice of elementary formula), Chapter 9
(chain drift), Chapter 6 (replacement), Chapter 11 (seasonality).

## Where they appear

The Diagnostics page, the findings narrative (`engine/insights.py` ranks
them by materiality into plain-English findings), and the deck and report.
