# Revision analysis (`engine/revision.py`)

## What the module computes

| Quantity | Definition |
|---|---|
| Triangle | one row per reference period, one column per vintage: what each vintage said about each period |
| Revisions | the successive differences along each row, in index points |
| MR | mean revision, signed — the bias if there is one |
| MAR | mean absolute revision — the size of the typical change, whichever way it went |
| Bias test | two-sided one-sample t-test of the mean revision against zero |
| Published vs current | per period: the first vintage that had a figure, the latest that has one, and the difference |

MAR large with MR near zero is noise; the two close together is a systematic
direction. A single number could not distinguish them, which is why both are
reported.

A revision is not an error. A first estimate is made on the data that had
arrived by the deadline, later estimates on more of it, and the difference
is the price of publishing early. What a reader is entitled to know is how
big that price has been and whether it has a sign — because a first estimate
revised up two years in three is not early, it is biased, and the correction
is to the process rather than to any one figure.

## Built on the registry, not beside it

There is no store of past publications here. A vintage **is** a registered
run: its input parquet, its configuration, its code version, its approval,
re-run by `core.registry.reproduce` from exactly that input and configuration
with the code running now; replaying it with the original code means checking
out its recorded commit. A separate table of
"what we published last time" would be a second version of the truth,
unreproducible, and the first thing to drift.

`core.registry.correct_run` (Phase 10) registers a correction as a new row
pointing back at the one it supersedes, with a mandatory reason, and never
modifies the original. `core.registry.vintage_chain` walks that linked list
in **both** directions from wherever the caller entered it — back through
`supersedes_run_id` to the original, then forward through whatever superseded
each one. Entering at the latest vintage and getting only that vintage back
would be the natural way to write a revision analysis that silently ignored
every revision.

`vintages_from_registry` then re-executes each vintage from its own stored
input and configuration, which is the only way to be sure the series being
compared is the one that vintage actually published rather than today's code
applied to an old file. It is also a whole pipeline per vintage, which is
what `max_vintages` is for.

## Reading the triangle

A cell is empty where that vintage had nothing to say about that period —
the ordinary shape of the thing, not a missing value to be filled: the first
vintage could not have an estimate for a month that had not happened when it
was compiled. A period that first appears in a later vintage contributes no
revision for the step into it, because **appearing is not being revised**.

## The bias test and its sample

The null is that revisions average out: that publishing early costs
precision but not accuracy. The verdict is reported with `n` attached and
says so explicitly below three observations, because the failure mode of
this test is not a wrong p-value — it is a reader taking "not significant"
from nine revisions as evidence of no bias.

## Citation

OECD/Eurostat guidelines on revisions policy and analysis; the MR/MAR/bias
framework as set out in the *OECD Handbook on Data Revisions* and published
by statistical offices as a revisions triangle.

## Assumptions

Revisions are measured in index points of the published level, which assumes
the vintages are on a comparable base — they are, because each reproduces
its own `index_reference_period`, but a correction that also changed the
reference period would make the difference a rebasing rather than a revision
and this module would not know.

The t-test treats revisions as independent draws. Revisions to adjacent
reference periods within one vintage step are usually correlated (one late
return moves several months), so the test's effective sample is smaller than
`n` and its p-value is optimistic. This is stated here rather than corrected
for: the alternative is an autocorrelation model the number of vintages in
practice cannot identify.

## What the engine does not do

No revision analysis by horizon (first estimate against the estimate three
months later, six months later, and so on) — the triangle carries the
information but the standard horizon summaries are not computed. No
decomposition of a revision into its sources (late returns, methodological
change, reweighting). No real-time database of source data as it arrived, so
"what would we have said with today's method on that day's data" cannot be
answered, only "what did we say".
