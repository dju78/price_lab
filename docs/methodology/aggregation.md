# Aggregation and contributions (`engine/aggregation.py`)

## What it computes

**Aggregation** rolls category (or lower-level) indices up a classification
tree: each parent is the weighted arithmetic mean of its children,
`I_agg(t) = Σ w_i I_i(t) / Σ w_i`, applied bottom-up until the root has a
value. Parent weights are derived by summing children; a supplied parent
weight that disagrees with its children's sum is reported in `problems`, not
silently preferred, and a leaf with an index but no weight is reported as
excluded rather than dropped without comment.

**Contribution** of series *i* to the aggregate's change from `start` to
`end`, in percentage points:
`c_i = w_i (I_i(end) − I_i(start)) / Σ_j w_j I_j(start) × 100`. These sum to
the aggregate's own percentage change exactly, which is why the aggregate is
arithmetic: a geometric aggregate has no exact additive decomposition.

The **equally weighted geometric aggregate** the "All items" series uses when
no expenditure weights are supplied is migrated here from `build_all`
unchanged: `exp(mean(log I_i))`. It is indicative only, and every method
note says so.

## Citation

CPI Manual 2020, Chapter 9 (aggregation at higher levels, paras 9.5–9.24),
Chapter 8 para. 8.3 (two-stage compilation). Contributions: Chapter 9,
"contributions to change", and the additivity requirement of Appendix 2,
test 4 of the platform specification, proven on random four-level trees in
`tests/test_axioms.py`.

## Assumptions

Weights are non-negative (a negative weight is refused outright), sum
coherently up the tree, and refer to the weight reference period. Weights
need not be normalised; the `Σw` terms cancel in the contribution formula.

## Known biases

An upper-level Laspeyres-type aggregate with fixed weights inherits the
substitution bias described in [bilateral.md](bilateral.md); the equally
weighted aggregate assumes every category matters equally, which no
collection without weights can support.
