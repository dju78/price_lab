"""Weighted aggregation up a classification tree, and the contributions
that decompose the headline change.

Two separate jobs that are easy to conflate:

*Aggregation* answers "what is the index for Food, given the indices for
its classes?" -- a weighted arithmetic mean of the children, applied
bottom-up until the root has a value.

*Contribution* answers "how much of the headline's 0.4 percent rise came
from Food?" -- and the answer has to add up. A decomposition whose parts
do not sum to the whole is not a decomposition; it is a set of loosely
related numbers that invite the reader to do arithmetic that will not
work. `contributions` below sums to the headline change exactly, to
floating-point precision, and `tests/test_axioms.py` proves it on random
trees rather than on a convenient example.

The equally weighted geometric aggregate that `engine.index.build_all` has
always used is migrated here rather than reimplemented: it is still what a
collection with no expenditure weights gets, it still produces exactly the
numbers it always did, and `build_all` now calls this module for it. What
is new is the weighted path, which needs weights and a tree and until
Phase 2 had neither.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

import numpy as np
import pandas as pd

# A pure function over plain mappings -- no database, no ORM -- so reusing
# it here keeps one definition of the weight-sum rule rather than a second
# copy that could drift from the one the ingestion layer validates against.
from ..data.classification import validate_weight_hierarchy

__all__ = [
    "AggregationResult",
    "aggregate_tree",
    "contributions",
    "equally_weighted_aggregate",
    "validate_weight_hierarchy",
    "weighted_aggregate",
]


class WeightError(ValueError):
    """Weights are missing, negative, or do not sum coherently across the
    tree they are supposed to describe."""


@dataclass
class AggregationResult:
    indices: pd.DataFrame
    """Every node of the tree, leaves as supplied and parents computed,
    one column per node."""
    weights: pd.Series
    """The weight actually used for each node, including the parent
    weights derived by summing children where none was supplied."""
    problems: list[str]
    """Weight-hierarchy violations found on the way up, reported rather
    than absorbed: a parent whose supplied weight disagrees with its
    children's sum is a fact about the weights file, and silently
    preferring one over the other hides it."""


def equally_weighted_aggregate(I: pd.DataFrame) -> pd.Series:
    """Unweighted geometric mean across columns.

    Migrated verbatim from `engine.index.build_all`, which still calls it,
    so the "All items" series of every existing run is unchanged. This is
    what a collection with no expenditure weights gets, and it is
    indicative only: it says every category matters equally, which is a
    claim about the world that no collection without weights can support.
    """
    return cast("pd.Series", np.exp(np.log(I).mean(axis=1)))


def weighted_aggregate(I: pd.DataFrame, weights: Mapping[str, float]) -> pd.Series:
    """Weighted arithmetic mean of the supplied indices.

        I_agg(t) = sum_i( w_i * I_i(t) ) / sum_i( w_i )

    Arithmetic rather than geometric because this is the form that makes
    the contributions below decompose exactly: a geometric aggregate has
    no exact additive decomposition, only approximations that leave a
    residual someone has to explain away.

    Columns with no weight are dropped rather than defaulted to zero or to
    the mean -- a category the weights file does not mention is a gap in
    the weights, not a category worth nothing, and quietly assigning it a
    number would bury that.
    """
    usable = [c for c in I.columns if c in weights and np.isfinite(weights[c])]
    if not usable:
        return pd.Series(np.nan, index=I.index)
    w = np.array([float(weights[c]) for c in usable])
    if w.sum() <= 0:
        return pd.Series(np.nan, index=I.index)
    weighted_sum: pd.Series = (I[usable] * w).sum(axis=1, min_count=1)
    return weighted_sum / float(w.sum())


def contributions(
    I: pd.DataFrame,
    weights: Mapping[str, float],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.Series:
    """Each column's contribution, in percentage points, to the weighted
    aggregate's change from `start` to `end`.

        c_i = w_i * (I_i(end) - I_i(start)) / sum_j( w_j * I_j(start) ) * 100

    These sum to the aggregate's own percentage change exactly, which is
    not a happy accident but the reason `weighted_aggregate` is arithmetic:

        sum_i c_i = [sum_i w_i I_i(end) - sum_i w_i I_i(start)]
                    / sum_j w_j I_j(start) * 100
                  = (I_agg(end) / I_agg(start) - 1) * 100

    The sum(w) terms cancel, so the result does not depend on the weights
    being normalised.
    """
    usable = [c for c in I.columns if c in weights and np.isfinite(weights[c])]
    if not usable:
        return pd.Series(dtype=float)
    w = np.array([float(weights[c]) for c in usable])
    start_levels = I.loc[start, usable].to_numpy(dtype=float)
    end_levels = I.loc[end, usable].to_numpy(dtype=float)
    base = float((start_levels * w).sum())
    if not np.isfinite(base) or base == 0:
        return pd.Series(np.nan, index=usable)
    return pd.Series(w * (end_levels - start_levels) / base * 100.0, index=usable)


def aggregate_tree(
    leaf_indices: pd.DataFrame,
    leaf_weights: Mapping[str, float],
    parent_of: Mapping[str, str | None],
    *,
    supplied_parent_weights: Mapping[str, float] | None = None,
    tolerance: float = 1e-6,
) -> AggregationResult:
    """Roll leaf indices up every level of a classification tree.

    `parent_of` maps each node's code to its parent's code, or to None for
    a root -- the shape `data.classification.parent_map` produces from a
    loaded COICOP (or user-defined) tree. Each parent's index is the
    weighted arithmetic mean of its children, and each parent's weight is
    the sum of its children's, computed depth-first from the leaves.

    `supplied_parent_weights`, when given, is checked against the derived
    sums rather than used in place of them: a weights file that states
    both the class weights and the division totals is asserting something
    that can be false, and `problems` reports it where it is false. The
    derived sum is what gets used, because an aggregate that does not
    equal the sum of its parts cannot produce contributions that add up.
    """
    nodes = set(parent_of) | set(leaf_indices.columns)
    children: dict[str, list[str]] = {n: [] for n in nodes}
    for node, parent in parent_of.items():
        if parent is not None:
            children.setdefault(parent, []).append(node)

    indices: dict[str, pd.Series] = {
        c: leaf_indices[c] for c in leaf_indices.columns}
    weights: dict[str, float] = {
        c: float(leaf_weights[c]) for c in leaf_indices.columns
        if c in leaf_weights and np.isfinite(leaf_weights[c])}

    negative = sorted(c for c, w in weights.items() if w < 0)
    if negative:
        raise WeightError(
            f"negative weights on {negative}: a negative expenditure weight has no "
            "interpretation in a consumption basket, and it would silently invert that "
            "item's contribution to the headline.")

    def depth(node: str) -> int:
        seen, d = set(), 0
        current: str | None = node
        while current is not None and current in parent_of and current not in seen:
            seen.add(current)
            current = parent_of[current]
            d += 1
        return d

    # Deepest first, so every parent is reached only after all of its own
    # children already have an index and a weight.
    for node in sorted(nodes, key=depth, reverse=True):
        kids = [k for k in children.get(node, []) if k in indices]
        if not kids or node in indices:
            continue
        kid_weights = {k: weights[k] for k in kids if k in weights}
        if not kid_weights:
            continue
        indices[node] = weighted_aggregate(pd.DataFrame({k: indices[k] for k in kids}),
                                           kid_weights)
        weights[node] = float(sum(kid_weights.values()))

    problems = validate_weight_hierarchy(weights, parent_of, tolerance)
    # A leaf with an index but no usable weight contributes to nothing
    # above it. That is the right arithmetic (see `weighted_aggregate`),
    # but it has to be said: a category the weights file forgot is a gap in
    # the weights, and an aggregate quietly computed without it looks
    # exactly like one computed with it.
    for leaf in leaf_indices.columns:
        if leaf not in weights:
            problems.append(
                f"{leaf}: has an index but no finite weight, so it was excluded from every "
                "aggregate above it; supply a weight or drop the series deliberately")
    if supplied_parent_weights:
        for node, supplied in supplied_parent_weights.items():
            derived = weights.get(node)
            if derived is not None and abs(derived - float(supplied)) > tolerance:
                problems.append(
                    f"{node}: supplied weight {float(supplied):.6g} does not equal the sum of "
                    f"its children's weights ({derived:.6g}); the children's sum was used, "
                    "because an aggregate that is not the sum of its parts cannot decompose "
                    "into contributions that add up")

    ordered = sorted(indices, key=lambda n: (depth(n), n))
    return AggregationResult(
        indices=pd.DataFrame({n: indices[n] for n in ordered}),
        weights=pd.Series({n: weights[n] for n in ordered if n in weights}),
        problems=problems,
    )


def contribution_table(
    result: AggregationResult,
    start: pd.Timestamp,
    end: pd.Timestamp,
    nodes: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Contributions plus the pieces a reader needs to check them: the
    weight, the level at each end, and the node's own percentage change
    alongside its contribution to the aggregate's.

    A category can move a long way and contribute almost nothing (small
    weight) or barely move and dominate the headline (large weight), and
    the contribution alone does not distinguish those two stories.
    """
    cols = list(nodes) if nodes is not None else [
        c for c in result.indices.columns if c in result.weights.index]
    weights = {str(k): float(v) for k, v in result.weights.items()}
    contrib = contributions(result.indices[cols], weights, start, end)
    level_start = result.indices.loc[start, cols]
    level_end = result.indices.loc[end, cols]
    return pd.DataFrame({
        "weight": result.weights.reindex(cols),
        "level_start": level_start,
        "level_end": level_end,
        "change_pct": (level_end / level_start - 1.0) * 100.0,
        "contribution_pp": contrib,
    })
