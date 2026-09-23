"""Decomposition: rates of change, contributions, core measures, base
effects, diffusion and dispersion.

What an analyst asks first is rarely "what is the index level". It is "how
fast are prices rising, what is driving it, and is it broad or narrow".
This module answers those three questions from a set of component indices
and their weights, and every answer is built so that it can be checked:

rates           period on period, year on year, annualised, three months on
                the previous three, and cumulative change. Each is a
                different question, and a headline that quotes one without
                saying which invites the reader to compare it with another.
contributions   each node's share of the headline change, at every level of
                the classification tree, built on `engine.aggregation` so
                there is one definition of the aggregate and one of a
                contribution. They reconcile to the headline to eight
                decimal places, and `TreeContributions.residual_pp` reports
                how far from exact they are rather than asserting it.
core measures   exclusion based, trimmed mean, weighted median, variance
                weighted and sticky price. Each states its parameters and
                the data it needs, and `core_measure_availability` says
                which can run on the data in hand and why the others
                cannot -- the same contract `engine.index.formula_availability`
                gives the formula selector.
base effects    the year-on-year rate split into the carry-over already
                accumulated by December and the impulse since, and the
                month-to-month change in that rate split into this month's
                movement and the base effect of last year's dropping out.
                Both splits are exact identities, not approximations.
diffusion       how many components are rising, and what share of the
                basket is rising faster than a threshold.
dispersion      the weighted spread and skewness of component price changes.

A note on weights. Every weighted measure here uses *effective* weights:
a component's expenditure weight times its index level at the start of the
comparison, normalised. That is what a fixed-basket (Laspeyres-type)
aggregate weights its components' changes by, so a weighted mean of
component changes under these weights equals the aggregate's change
exactly -- which is what makes a zero-trim trimmed mean equal the headline,
and what the tests check. Across a chain link (a year-on-year comparison
spanning December in an annually re-weighted index, for example) one set of
fixed weights is an approximation, and results that span one say so;
`ribe_contributions` gives the exact contributions across the link by the
published treatment (`RIBE_SOURCE`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

import numpy as np
import pandas as pd

from .aggregation import aggregate_tree, contributions, weighted_aggregate

__all__ = [
    "BaseEffects",
    "CORE_MEASURES",
    "ChainLinkContributions",
    "CoreMeasure",
    "DecompositionError",
    "TreeContributions",
    "base_effects",
    "chain_linked_aggregate",
    "contributions_over_time",
    "core_measure_availability",
    "dispersion",
    "diffusion",
    "exclusion_measure",
    "rates",
    "ribe_contributions",
    "sticky_price_measure",
    "tree_contributions",
    "trimmed_mean",
    "variance_weighted",
    "weighted_median",
]

#: Contributions must add to the headline when both are rounded to this
#: many decimal places.
RECONCILIATION_DECIMALS = 8
RECONCILIATION_TOLERANCE_PP = 0.5 * 10.0 ** -RECONCILIATION_DECIMALS


class DecompositionError(ValueError):
    """The data cannot support the measure asked of it, and the message
    says what is missing."""


def _cell(frame: pd.DataFrame, row: Any, column: Any) -> float:
    """One value of a frame as a float. pandas' stubs type a scalar lookup
    as a union of every scalar pandas can hold; the columns read here are
    numeric by construction."""
    return float(cast(Any, frame.at[row, column]))


# ---------------------------------------------------------------------
# Rates of change
# ---------------------------------------------------------------------
RATE_LABELS: dict[str, str] = {
    "period_on_period_pct": "Period on period, %",
    "year_on_year_pct": "Year on year, %",
    "annualised_pct": "Period on period, annualised, %",
    "three_month_on_three_month_pct": "Three months on the previous three, %",
    "three_month_on_three_month_annualised_pct": "Three months on the previous three, "
                                                 "annualised, %",
    "cumulative_pct": "Cumulative since the base period, %",
}


def _check_frequency(periods_per_year: int) -> None:
    if periods_per_year not in (4, 12):
        raise DecompositionError(
            f"rates are defined here for monthly (12) and quarterly (4) series, not "
            f"{periods_per_year} periods a year")


def rates(series: pd.Series, periods_per_year: int = 12, *,
          cumulative_from: pd.Timestamp | None = None) -> pd.DataFrame:
    """Every standard rate of change of one index series, side by side.

    period on period      I(t) / I(t-1) - 1
    year on year          I(t) / I(t-p) - 1, p periods a year
    annualised            (I(t) / I(t-1))^p - 1: this period's movement, as if
                          it continued all year. Volatile by construction.
    three on three        the average of the latest three months over the
                          average of the three before, and that annualised;
                          for a quarterly series, one quarter on the one
                          before, which is the same question
    cumulative            I(t) / I(base) - 1, from `cumulative_from` (the
                          first period by default)

    All in percent. A period with no comparison period is NaN rather than
    dropped, so every column lines up with the index.
    """
    _check_frequency(periods_per_year)
    s = pd.Series(series, dtype=float)
    if s.dropna().empty:
        raise DecompositionError("the series has no values to compute rates from")
    window = periods_per_year // 4
    base = cumulative_from if cumulative_from is not None else s.first_valid_index()
    if base not in s.index:
        raise DecompositionError(f"the cumulative base period {base!s} is not in the series")
    ratio = s / s.shift(1)
    average = s.rolling(window).mean()
    three = average / average.shift(window)
    return pd.DataFrame({
        "level": s,
        "period_on_period_pct": (ratio - 1.0) * 100.0,
        "year_on_year_pct": (s / s.shift(periods_per_year) - 1.0) * 100.0,
        "annualised_pct": (ratio ** periods_per_year - 1.0) * 100.0,
        "three_month_on_three_month_pct": (three - 1.0) * 100.0,
        "three_month_on_three_month_annualised_pct":
            (three ** (periods_per_year / window) - 1.0) * 100.0,
        "cumulative_pct": (s / float(cast(Any, s.loc[base])) - 1.0) * 100.0,
    })


# ---------------------------------------------------------------------
# Contributions at every level of the tree
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class TreeContributions:
    """Every node's contribution to the headline change between two periods."""

    table: pd.DataFrame
    """One row per node: parent, depth, weight, the level at each end, the
    node's own change, its contribution to its parent's change and its
    contribution to the headline's, in percentage points."""
    root: str
    start: pd.Timestamp
    end: pd.Timestamp
    headline_change_pct: float
    residual_pp: float
    """The largest gap, anywhere in the tree, between a parent's
    contribution to the headline and the sum of its children's -- and
    between the headline change and the sum of the top level. Reported,
    not assumed: zero to floating-point precision when the aggregate is the
    weighted arithmetic mean of its parts."""
    problems: tuple[str, ...] = ()
    indices: pd.DataFrame | None = None
    """Every node's index, leaves and computed parents, as rolled up."""
    weights: pd.Series | None = None

    @property
    def reconciles(self) -> bool:
        return bool(np.isfinite(self.residual_pp)
                    and self.residual_pp < RECONCILIATION_TOLERANCE_PP)

    def children(self, node: str) -> pd.DataFrame:
        return self.table[self.table["parent"] == node]


def tree_contributions(
    leaf_indices: pd.DataFrame,
    leaf_weights: Mapping[str, float],
    parent_of: Mapping[str, str | None],
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    supplied_parent_weights: Mapping[str, float] | None = None,
) -> TreeContributions:
    """Contributions to the headline change at every level of a tree.

    The tree is rolled up by `engine.aggregation.aggregate_tree`, and each
    parent's children are decomposed by `engine.aggregation.contributions`
    -- the same two functions the index build uses, so there is one
    definition of the aggregate and one of a contribution. A child's
    contribution to its parent is then rescaled to the headline:

        c_to_headline(i) = c_to_parent(i) * W_p I_p(start) / (W_root I_root(start))

    which works because each parent is the weighted mean of its children
    and carries the sum of their weights, so W_p I_p = sum_i W_i I_i at
    every node. The contributions therefore nest: a division's children sum
    to the division's contribution, the divisions sum to the headline
    change, at every level at once.
    """
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    rolled = aggregate_tree(leaf_indices, leaf_weights, parent_of,
                            supplied_parent_weights=supplied_parent_weights)
    roots = [n for n, p in parent_of.items() if p is None and n in rolled.indices.columns]
    if len(roots) != 1:
        raise DecompositionError(
            f"the tree must have exactly one root with an index to decompose; it has "
            f"{len(roots)} ({', '.join(roots) or 'none'})")
    root = roots[0]
    levels, weights = rolled.indices, rolled.weights
    for when, name in ((start, "start"), (end, "end")):
        if when not in levels.index:
            raise DecompositionError(f"the {name} period {when:%Y-%m} is not in the indices")

    children: dict[str, list[str]] = {}
    for node, parent in parent_of.items():
        if parent is not None and node in levels.columns and node in weights.index:
            children.setdefault(parent, []).append(node)

    def depth(node: str) -> int:
        d, current = 0, parent_of.get(node)
        while current is not None:
            d, current = d + 1, parent_of.get(current)
        return d

    root_base = float(weights[root]) * _cell(levels, start, root)
    headline = (_cell(levels, end, root) / _cell(levels, start, root) - 1.0) * 100.0
    to_parent: dict[str, float] = {}
    to_headline: dict[str, float] = {root: headline}
    for parent, kids in children.items():
        if parent not in levels.columns or parent not in weights.index:
            continue
        shares = contributions(levels[kids], {k: float(weights[k]) for k in kids}, start, end)
        scale = float(weights[parent]) * _cell(levels, start, parent) / root_base
        for kid, value in shares.items():
            to_parent[str(kid)] = float(value)
            to_headline[str(kid)] = float(value) * scale

    # Each child's scale uses its own parent's level directly, so the result
    # does not depend on the order in which the parents were visited.
    nodes = sorted((n for n in levels.columns if n in weights.index), key=lambda n: (depth(n), n))
    table = pd.DataFrame({
        "parent": [parent_of.get(n) for n in nodes],
        "depth": [depth(n) for n in nodes],
        "weight": [float(weights[n]) for n in nodes],
        "level_start": [_cell(levels, start, n) for n in nodes],
        "level_end": [_cell(levels, end, n) for n in nodes],
        "change_pct": [(_cell(levels, end, n) / _cell(levels, start, n) - 1.0) * 100.0
                       for n in nodes],
        "contribution_to_parent_pp": [to_parent.get(n, np.nan) for n in nodes],
        "contribution_to_headline_pp": [to_headline.get(n, np.nan) for n in nodes],
    }, index=pd.Index(nodes, name="node"))

    residual = 0.0
    for parent, kids in children.items():
        if parent not in table.index:
            continue
        summed = float(table.loc[kids, "contribution_to_headline_pp"].sum())
        residual = max(residual, abs(summed - _cell(table, parent,
                                                   "contribution_to_headline_pp")))
    return TreeContributions(table=table, root=root, start=start, end=end,
                             headline_change_pct=headline, residual_pp=residual,
                             problems=tuple(rolled.problems), indices=levels, weights=weights)


def contributions_over_time(components: pd.DataFrame, weights: Mapping[str, float], *,
                            horizon: int = 1) -> pd.DataFrame:
    """Each component's contribution to the aggregate's change over
    `horizon` periods, for every period that has a comparison.

    One row per period, one column per component, in percentage points;
    each row sums to the change of `weighted_aggregate(components, weights)`
    over the same horizon. `engine.aggregation.contributions` computes every
    row, so this is the single-period decomposition repeated, not a second
    formula.
    """
    if horizon < 1:
        raise DecompositionError("a contribution needs a horizon of at least one period")
    periods = list(components.index)
    rows: dict[pd.Timestamp, pd.Series] = {}
    for i in range(horizon, len(periods)):
        rows[pd.Timestamp(periods[i])] = contributions(components, weights, periods[i - horizon],
                                                       periods[i])
    if not rows:
        return pd.DataFrame(columns=components.columns, dtype=float)
    return pd.DataFrame(rows).T


# ---------------------------------------------------------------------
# Contributions across a chain link (Ribe)
# ---------------------------------------------------------------------
#: The published treatment implemented by `ribe_contributions`.
RIBE_SOURCE = (
    "OECD, 'OECD calculation of contributions to overall annual inflation' (May 2018, "
    "updated March 2022), section 3, following Walschots (2016), 'Contributions to and "
    "impacts on inflation', Statistics Netherlands; named the Ribe contribution by Balk and "
    "Mehrhoff, 'Index calculation', chapter 8 of Eurostat's HICP Methodological Manual. "
    "Eurostat's published HICP contributions (dataset prc_hicp_ctrb) use it.")


@dataclass(frozen=True)
class ChainLinkContributions:
    """Contributions to the annual rate of an annually chain-linked index."""

    contributions: pd.DataFrame
    """Per period (rows) and component (columns), percentage points."""
    since_link: pd.DataFrame
    """The part from December of last year to this month, on this year's
    weights (the OECD note's terms 1-3)."""
    before_link: pd.DataFrame
    """The part from this month last year to December of last year, on last
    year's weights (terms 4-6). Zero in December."""
    aggregate: pd.Series
    annual_rate_pct: pd.Series
    residual_pp: float
    """The largest gap between the sum of the contributions and the annual
    rate. Zero to rounding when the aggregate is the one the components and
    weights produce; not zero when a published aggregate compiled from
    unrounded data is supplied, and then it is reported, not absorbed."""
    link_month: int
    source: str = RIBE_SOURCE


def chain_linked_aggregate(components: pd.DataFrame,
                           weights_by_year: Mapping[int, Mapping[str, float]], *,
                           link_month: int = 12) -> pd.Series:
    """The annually chain-linked Laspeyres-type aggregate of `components`.

    Within each year y the aggregate moves with the weighted mean of the
    components' relatives to the link month (December) of y - 1, on the
    weights for year y -- the weights "used for the link from December of
    year y - 1 until December of year y" in the OECD note's words -- and the
    links are multiplied together. The first link month in the data is
    100.
    """
    frame = components.sort_index()
    index = pd.DatetimeIndex(frame.index)
    links = [t for t in index if t.month == link_month]
    if not links:
        raise DecompositionError(f"no link month ({link_month}) in the component indices")
    level = {links[0]: 100.0}
    values: dict[pd.Timestamp, float] = {links[0]: 100.0}
    for t in index[index > links[0]]:
        link = pd.Timestamp(t.year - 1 if t.month <= link_month else t.year, link_month, 1)
        year = link.year + 1
        if link not in level or year not in weights_by_year:
            continue
        w = _normalised(weights_by_year[year], frame.columns)
        relatives = _row(frame, t, list(w)) / _row(frame, link, list(w))
        values[t] = level[link] * float((relatives * pd.Series(w)).sum())
        if t.month == link_month:
            level[t] = values[t]
    return pd.Series(values, dtype=float).reindex(index)


def _row(frame: pd.DataFrame, period: pd.Timestamp, columns: list[str]) -> pd.Series:
    """One period's values for the named columns, as a float Series."""
    return pd.Series(frame.loc[period, columns], dtype=float)


def _normalised(weights: Mapping[str, float], columns: pd.Index) -> dict[str, float]:
    usable = {str(c): float(weights[str(c)]) for c in columns
              if str(c) in weights and np.isfinite(weights[str(c)])}
    total = sum(usable.values())
    if total <= 0:
        raise DecompositionError("the weights for a year sum to zero or are missing")
    return {c: v / total for c, v in usable.items()}


def ribe_contributions(components: pd.DataFrame,
                       weights_by_year: Mapping[int, Mapping[str, float]], *,
                       aggregate: pd.Series | None = None,
                       link_month: int = 12) -> ChainLinkContributions:
    """Contributions to the annual rate across the annual re-weighting.

    For month m of year y, with P the chain-linked indices, W the
    normalised weights (W^{y-1,12} used from December y-1, W^{y-2,12} from
    December y-2) and TOT the aggregate:

        C_j = [P_TOT(y-1,12) / P_TOT(y-1,m)] W_j^{y-1,12} [P_j(y,m) - P_j(y-1,12)] / P_j(y-1,12)
            + [P_TOT(y-2,12) / P_TOT(y-1,m)] W_j^{y-2,12} [P_j(y-1,12) - P_j(y-1,m)] / P_j(y-2,12)

    The first bracket is the movement since the link, on this year's
    weights; the second the movement from this month last year up to the
    link, on last year's. They sum over j to the aggregate's annual rate
    exactly, because the aggregate is built from the same weights over the
    same two links -- which is why this, and not a year-on-year
    contribution on one set of weights, is the treatment for a comparison
    that spans a re-weighting. `RIBE_SOURCE` names the published sources.

    `aggregate` defaults to `chain_linked_aggregate` of the same components
    and weights. Passing a published aggregate reproduces the published
    contributions, and the residual against its own annual rate is then
    reported rather than forced to zero.
    """
    frame = components.sort_index().astype(float)
    total = aggregate if aggregate is not None else chain_linked_aggregate(
        frame, weights_by_year, link_month=link_month)
    total = pd.Series(total, dtype=float)
    total.index = pd.DatetimeIndex(total.index)
    frame.index = pd.DatetimeIndex(frame.index)
    since: dict[pd.Timestamp, pd.Series] = {}
    before: dict[pd.Timestamp, pd.Series] = {}
    rate: dict[pd.Timestamp, float] = {}
    for t in frame.index:
        year, month = t.year, t.month
        # The two links the comparison spans: the last one before t, and
        # the one before that. For December the comparison is exactly one
        # link long and the second part is zero.
        link_1 = pd.Timestamp(year - 1 if month <= link_month else year, link_month, 1)
        link_0 = pd.Timestamp(link_1.year - 1, link_month, 1)
        year_ago = pd.Timestamp(year - 1, month, 1)
        needed = (link_1, link_0, year_ago)
        weights_1, weights_0 = link_1.year + 1, link_0.year + 1
        if any(p not in frame.index or p not in total.index for p in needed) \
                or weights_1 not in weights_by_year or weights_0 not in weights_by_year:
            continue
        w1 = pd.Series(_normalised(weights_by_year[weights_1], frame.columns))
        w0 = pd.Series(_normalised(weights_by_year[weights_0], frame.columns))
        cols = list(w1.index)
        denominator = float(total[year_ago])
        now, at_link, at_link_0, then = (_row(frame, p, cols)
                                         for p in (t, link_1, link_0, year_ago))
        since[t] = float(total[link_1]) / denominator * w1 * (now - at_link) / at_link * 100.0
        w0 = w0.reindex(cols).fillna(0.0)
        before[t] = (float(total[link_0]) / denominator * w0 * (at_link - then) / at_link_0
                     * 100.0)
        rate[t] = (float(total[t]) / denominator - 1.0) * 100.0
    if not since:
        raise DecompositionError(
            "no period has the two links, the year-ago month and both years' weights that a "
            "contribution across a chain link needs: supply at least two years of weights and "
            "indices from December two years before the first month to decompose")
    first = pd.DataFrame(since).T
    second = pd.DataFrame(before).T
    both = first + second
    annual = pd.Series(rate)
    residual = float((both.sum(axis=1) - annual).abs().max())
    return ChainLinkContributions(
        contributions=both, since_link=first, before_link=second,
        aggregate=total, annual_rate_pct=annual, residual_pp=residual, link_month=link_month)


# ---------------------------------------------------------------------
# Core and underlying measures
# ---------------------------------------------------------------------
CORE_MEASURES: tuple[str, ...] = ("exclusion", "trimmed_mean", "weighted_median",
                                  "variance_weighted", "sticky_price")

CORE_LABELS: dict[str, str] = {
    "exclusion": "Exclusion-based",
    "trimmed_mean": "Trimmed mean",
    "weighted_median": "Weighted median",
    "variance_weighted": "Variance weighted",
    "sticky_price": "Sticky price",
}

CORE_REQUIREMENTS: dict[str, str] = {
    "exclusion": "component indices, their expenditure weights, and at least one component "
                 "left once the exclusions are removed",
    "trimmed_mean": "component indices and expenditure weights for at least three components",
    "weighted_median": "component indices and expenditure weights for at least three components",
    "variance_weighted": "component indices, expenditure weights, and a history of at least the "
                         "volatility window before the first period it reports",
    "sticky_price": "item-level prices -- an item identifier and a price in consecutive "
                    "periods -- from which each component's frequency of price change is "
                    "measured; published component indices do not carry it",
}

#: Fewest components a distribution-based measure is computed on. Below
#: three there is no middle to trim towards.
MIN_COMPONENTS = 3


@dataclass(frozen=True)
class CoreMeasure:
    """One core or underlying inflation measure, with what produced it."""

    key: str
    series: pd.Series
    """Percent change over `parameters['horizon']` periods, per period."""
    parameters: Mapping[str, Any]
    requirements: str
    notes: tuple[str, ...] = ()
    detail: pd.DataFrame | None = None

    @property
    def name(self) -> str:
        return CORE_LABELS[self.key]

    @property
    def label(self) -> str:
        """The measure and its parameters, in one phrase: a core rate quoted
        without its trim or its exclusions is a different number from the
        same measure with other settings, and nothing in the value says so."""
        shown = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in self.parameters.items()
                          if not isinstance(v, (list, tuple)) or len(v) <= 6)
        return f"{self.name} ({shown})"


def _require_weights(components: pd.DataFrame, weights: Mapping[str, float] | None,
                     measure: str) -> dict[str, float]:
    if weights is None:
        raise DecompositionError(
            f"{CORE_LABELS.get(measure, measure)} needs expenditure weights: every core "
            "measure reweights a basket, and a collection without weights has no basket to "
            "reweight. Supply a weight column, or use an official source that publishes "
            "item weights")
    usable = {str(c): float(weights[str(c)]) for c in components.columns
              if str(c) in weights and np.isfinite(weights[str(c)]) and weights[str(c)] > 0}
    if not usable:
        raise DecompositionError("no component has a positive, finite weight")
    return usable


def _changes(components: pd.DataFrame, horizon: int) -> pd.DataFrame:
    return (components / components.shift(horizon) - 1.0) * 100.0


def _effective_weights(components: pd.DataFrame, weights: Mapping[str, float],
                       horizon: int) -> pd.DataFrame:
    """w_i * I_i(t - horizon), per period; not normalised, since every use
    below divides by its own row total over the components it keeps."""
    columns = [c for c in components.columns if c in weights]
    w = pd.Series({c: float(weights[c]) for c in columns})
    return components[columns].shift(horizon) * w


def _horizon_note(horizon: int, periods_per_year: int) -> tuple[str, ...]:
    if horizon >= periods_per_year:
        return ("a comparison over a full year spans at least one chain link in an annually "
                "re-weighted index; one set of fixed weights across it is an approximation",)
    return ()


def core_measure_availability(
    components: pd.DataFrame, weights: Mapping[str, float] | None, *,
    horizon: int = 1, window: int = 24, panel: pd.DataFrame | None = None,
    exclude: Sequence[str] = (),
) -> dict[str, str | None]:
    """Which core measures the data can support: None for a measure that can
    run, otherwise the reason it cannot, written to be shown to the user.

    The same contract as `engine.index.formula_availability`: a measure is
    offered only when the data supports it, and one that is not offered
    says why rather than disappearing.
    """
    reasons: dict[str, str | None] = dict.fromkeys(CORE_MEASURES)
    if weights is None or not any(c in weights for c in components.columns):
        why = ("no expenditure weights: every core measure reweights a basket, and without "
               "weights there is no basket to reweight")
        return dict.fromkeys(CORE_MEASURES, why)
    weighted = [c for c in components.columns if c in weights]
    kept = [c for c in weighted if c not in set(exclude)]
    if not exclude:
        reasons["exclusion"] = "choose at least one component to exclude"
    elif not kept:
        reasons["exclusion"] = "the exclusions remove every weighted component"
    if len(weighted) < MIN_COMPONENTS:
        why = (f"only {len(weighted)} weighted component(s); a distribution needs at least "
               f"{MIN_COMPONENTS} to have a middle to trim towards")
        reasons["trimmed_mean"] = reasons["weighted_median"] = why
    history = len(components.index) - horizon
    if len(weighted) < 2:
        reasons["variance_weighted"] = "one component has no relative volatility to weight by"
    elif history <= window:
        reasons["variance_weighted"] = (
            f"{max(history, 0)} period(s) of changes, and the {window}-period volatility window "
            "needs more than that before it can report anything")
    if panel is None or not {"item_id", "period", "category", "price_clean"}.issubset(
            panel.columns):
        reasons["sticky_price"] = (
            "no item-level prices: a sticky-price measure classifies each component by how "
            "often its items' prices change, which only item-level data records. Published "
            "component indices do not carry it")
    return reasons


def _leaves_under(node: str, parent_of: Mapping[str, str | None]) -> set[str]:
    """Every node whose chain of parents passes through `node`, and `node`."""
    found = {node}
    for candidate in parent_of:
        current: str | None = candidate
        seen: set[str] = set()
        while current is not None and current not in seen:
            if current == node:
                found.add(candidate)
                break
            seen.add(current)
            current = parent_of.get(current)
    return found


def exclusion_measure(components: pd.DataFrame, weights: Mapping[str, float] | None,
                      exclude: Sequence[str], *, horizon: int = 1,
                      periods_per_year: int = 12,
                      parent_of: Mapping[str, str | None] | None = None) -> CoreMeasure:
    """The aggregate of what is left once the named components are removed,
    re-weighted over the remainder -- "all items excluding food and
    energy", with the exclusions named rather than implied.

    With `parent_of`, an exclusion may name any node of the tree, and
    excludes every component beneath it: "CP01" removes food's groups.
    """
    w = _require_weights(components, weights, "exclusion")
    tree = parent_of or {}
    removed: set[str] = set()
    unknown: list[str] = []
    for code in exclude:
        beneath = _leaves_under(code, tree) & set(components.columns) if tree else set()
        if code in components.columns:
            removed.add(code)
        elif beneath:
            removed |= beneath
        else:
            unknown.append(code)
    if unknown:
        raise DecompositionError(f"cannot exclude {sorted(unknown)}: no such component")
    kept = [c for c in components.columns if c in w and c not in removed]
    if not exclude:
        raise DecompositionError("an exclusion measure needs at least one exclusion")
    if not kept:
        raise DecompositionError("the exclusions remove every weighted component")
    aggregate = weighted_aggregate(components[kept], w)
    share = sum(w[c] for c in kept) / sum(w.values()) * 100.0
    return CoreMeasure(
        key="exclusion",
        series=((aggregate / aggregate.shift(horizon) - 1.0) * 100.0).rename("exclusion"),
        parameters={"horizon": horizon, "excluded": sorted(exclude),
                    "components_removed": len(removed),
                    "basket_share_kept_pct": round(share, 2)},
        requirements=CORE_REQUIREMENTS["exclusion"],
        notes=_horizon_note(horizon, periods_per_year))


def _trim_row(x: np.ndarray, w: np.ndarray, alpha: float) -> float:
    """Weighted trimmed mean of one period's changes.

    The distribution is sorted by change, weights are normalised to sum to
    one, and each component keeps whatever part of its weight lies between
    the cumulative weights `alpha` and `1 - alpha` -- the standard
    treatment (the Reserve Bank of Australia's, among others), in which a
    component straddling a trim point is kept in part rather than all or
    nothing. At `alpha = 0` this is the weighted mean, which equals the
    aggregate's change under effective weights.
    """
    ok = np.isfinite(x) & np.isfinite(w) & (w > 0)
    if ok.sum() == 0:
        return float("nan")
    order = np.argsort(x[ok], kind="stable")
    xs, ws = x[ok][order], w[ok][order] / w[ok].sum()
    upper = np.cumsum(ws)
    lower = upper - ws
    kept = np.clip(np.minimum(upper, 1.0 - alpha) - np.maximum(lower, alpha), 0.0, None)
    if kept.sum() <= 0:
        return float("nan")
    return float((kept * xs).sum() / kept.sum())


def trimmed_mean(components: pd.DataFrame, weights: Mapping[str, float] | None, *,
                 trim_pct: float = 15.0, horizon: int = 1,
                 periods_per_year: int = 12) -> CoreMeasure:
    """Weighted trimmed mean: `trim_pct` percent of the basket, by weight,
    is removed from each tail of the distribution of component changes and
    the weighted mean of the rest is reported."""
    w = _require_weights(components, weights, "trimmed_mean")
    if not 0.0 <= trim_pct < 50.0:
        raise DecompositionError(
            f"the trim is a percentage of the basket removed from each tail, so it must be at "
            f"least 0 and below 50; {trim_pct} was given (50 is the weighted median)")
    if len(w) < MIN_COMPONENTS:
        raise DecompositionError(
            f"a trimmed mean needs at least {MIN_COMPONENTS} weighted components, not {len(w)}")
    x = _changes(components[list(w)], horizon)
    ew = _effective_weights(components[list(w)], w, horizon)
    values = [_trim_row(x.loc[t].to_numpy(dtype=float), ew.loc[t].to_numpy(dtype=float),
                        trim_pct / 100.0) for t in x.index]
    return CoreMeasure(
        key="trimmed_mean", series=pd.Series(values, index=x.index, name="trimmed_mean"),
        parameters={"horizon": horizon, "trim_each_tail_pct": trim_pct},
        requirements=CORE_REQUIREMENTS["trimmed_mean"],
        notes=_horizon_note(horizon, periods_per_year))


def _median_row(x: np.ndarray, w: np.ndarray) -> float:
    """The change at which the cumulative weight first reaches one half.

    When the cumulative weight lands on one half exactly, at a boundary
    between two components, the median is the average of the two -- the
    same convention as the unweighted median of an even count.
    """
    ok = np.isfinite(x) & np.isfinite(w) & (w > 0)
    if ok.sum() == 0:
        return float("nan")
    order = np.argsort(x[ok], kind="stable")
    xs, ws = x[ok][order], w[ok][order] / w[ok].sum()
    cumulative = np.cumsum(ws)
    k = int(np.searchsorted(cumulative, 0.5 - 1e-12))
    if abs(cumulative[k] - 0.5) <= 1e-12 and k + 1 < len(xs):
        return float((xs[k] + xs[k + 1]) / 2.0)
    return float(xs[k])


def weighted_median(components: pd.DataFrame, weights: Mapping[str, float] | None, *,
                    horizon: int = 1, periods_per_year: int = 12) -> CoreMeasure:
    """The change of the component at the middle of the basket by weight."""
    w = _require_weights(components, weights, "weighted_median")
    if len(w) < MIN_COMPONENTS:
        raise DecompositionError(
            f"a weighted median needs at least {MIN_COMPONENTS} weighted components, not {len(w)}")
    x = _changes(components[list(w)], horizon)
    ew = _effective_weights(components[list(w)], w, horizon)
    values = [_median_row(x.loc[t].to_numpy(dtype=float), ew.loc[t].to_numpy(dtype=float))
              for t in x.index]
    return CoreMeasure(
        key="weighted_median", series=pd.Series(values, index=x.index, name="weighted_median"),
        parameters={"horizon": horizon},
        requirements=CORE_REQUIREMENTS["weighted_median"],
        notes=_horizon_note(horizon, periods_per_year))


def variance_weighted(components: pd.DataFrame, weights: Mapping[str, float] | None, *,
                      window: int = 24, horizon: int = 1,
                      periods_per_year: int = 12) -> CoreMeasure:
    """Each component's effective weight divided by the variance of its
    own changes over the `window` periods before the one being measured.

    Trailing and excluding the current period, so the measure can be
    computed in real time and a period's own shock does not lower its own
    weight. A component whose changes did not vary at all over the window
    would get an infinite weight; its volatility is floored at the smallest
    non-zero volatility among the components that period, and the floor is
    reported rather than applied silently.
    """
    w = _require_weights(components, weights, "variance_weighted")
    if window < 6:
        raise DecompositionError(
            f"a volatility window of {window} periods estimates a variance from too few "
            "changes to mean anything; use at least 6")
    columns = list(w)
    if len(columns) < 2:
        raise DecompositionError("one component has no relative volatility to weight by")
    x = _changes(components[columns], horizon)
    ew = _effective_weights(components[columns], w, horizon)
    sd = x.rolling(window, min_periods=window).std().shift(1)
    values: list[float] = []
    floored = 0
    for t in x.index:
        s = sd.loc[t].to_numpy(dtype=float)
        if not np.isfinite(s).any():
            values.append(float("nan"))
            continue
        positive = s[np.isfinite(s) & (s > 0)]
        if len(positive) and (s[np.isfinite(s)] <= 0).any():
            floored += int((s[np.isfinite(s)] <= 0).sum())
            s = np.where(np.isfinite(s) & (s <= 0), positive.min(), s)
        vw = ew.loc[t].to_numpy(dtype=float) / s ** 2
        xs = x.loc[t].to_numpy(dtype=float)
        ok = np.isfinite(vw) & np.isfinite(xs)
        values.append(float((vw[ok] * xs[ok]).sum() / vw[ok].sum()) if ok.any() else float("nan"))
    notes = list(_horizon_note(horizon, periods_per_year))
    if floored:
        notes.append(f"{floored} component-period(s) had no variation over the window; their "
                     "volatility was floored at the smallest non-zero one that period")
    return CoreMeasure(
        key="variance_weighted",
        series=pd.Series(values, index=x.index, name="variance_weighted"),
        parameters={"horizon": horizon, "window": window},
        requirements=CORE_REQUIREMENTS["variance_weighted"], notes=tuple(notes))


def price_change_frequency(panel: pd.DataFrame, *, group: str = "category",
                           price_col: str = "price_clean",
                           periods_per_year: int = 12) -> pd.DataFrame:
    """Per component, how often its items' prices change.

    Only consecutive observations of the same item are compared -- a gap
    in an item's record says nothing about whether its price changed in
    between -- and the implied duration is the reciprocal of the frequency,
    in months.
    """
    needed = {group, "item_id", "period", price_col}
    missing = sorted(needed - set(panel.columns))
    if missing:
        raise DecompositionError(
            f"the panel has no {missing} column(s): {CORE_REQUIREMENTS['sticky_price']}")
    frame = panel[[group, "item_id", "period", price_col]].dropna().copy()
    frame["period"] = pd.DatetimeIndex(frame["period"])
    frame = frame.sort_values([group, "item_id", "period"])
    months_between = 12 // periods_per_year
    previous = frame.groupby([group, "item_id"])[[price_col, "period"]].shift(1)
    step = ((frame["period"].dt.year - previous["period"].dt.year) * 12
            + frame["period"].dt.month - previous["period"].dt.month)
    consecutive = step == months_between
    changed = (frame[price_col] - previous[price_col]).abs() > 1e-9 * frame[price_col].abs()
    stats = pd.DataFrame({"comparisons": consecutive.groupby(frame[group]).sum(),
                          "changes": (changed & consecutive).groupby(frame[group]).sum()})
    stats["frequency"] = stats["changes"] / stats["comparisons"].where(stats["comparisons"] > 0)
    stats["duration_months"] = months_between / stats["frequency"].where(stats["frequency"] > 0)
    return stats


def sticky_price_measure(components: pd.DataFrame, weights: Mapping[str, float] | None,
                         panel: pd.DataFrame | None, *, threshold_months: float = 4.3,
                         horizon: int = 1, group: str = "category",
                         price_col: str = "price_clean",
                         periods_per_year: int = 12) -> CoreMeasure:
    """The aggregate of the components whose prices change slowly.

    A component is sticky when its items' prices last longer than
    `threshold_months` on average between changes. The default 4.3 months
    is the cut-off the Federal Reserve Bank of Atlanta's sticky-price CPI
    uses (Bryan and Meyer, 2010), itself the median duration in the US CPI
    microdata: sticky prices are set looking further ahead, which is the
    argument for reading them as a signal of expected inflation.
    """
    w = _require_weights(components, weights, "sticky_price")
    if panel is None:
        raise DecompositionError(CORE_REQUIREMENTS["sticky_price"])
    stats = price_change_frequency(panel, group=group, price_col=price_col,
                                   periods_per_year=periods_per_year)
    stats = stats[stats.index.isin(list(w))]
    stats["sticky"] = stats["duration_months"].fillna(np.inf) > threshold_months
    sticky = [str(c) for c in stats.index[stats["sticky"]]]
    if not sticky:
        raise DecompositionError(
            f"no component's prices last longer than {threshold_months} months between "
            "changes, so there is no sticky-price basket to aggregate")
    aggregate = weighted_aggregate(components[sticky], w)
    share = sum(w[c] for c in sticky) / sum(w.values()) * 100.0
    return CoreMeasure(
        key="sticky_price",
        series=((aggregate / aggregate.shift(horizon) - 1.0) * 100.0).rename("sticky_price"),
        parameters={"horizon": horizon, "threshold_months": threshold_months,
                    "sticky_components": sticky, "basket_share_sticky_pct": round(share, 2)},
        requirements=CORE_REQUIREMENTS["sticky_price"],
        notes=_horizon_note(horizon, periods_per_year), detail=stats)


# ---------------------------------------------------------------------
# Base effects
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class BaseEffects:
    """Two exact splits of the year-on-year rate.

    frame columns, all percent or percentage points:

    year_on_year_pct     I(t) / I(t-p) - 1
    carry_over_pp        (I(D) - I(t-p)) / I(t-p): the rise already in the
                         bag by December of last year (D), before this
                         year's prices have moved at all
    impulse_pp           (I(t) - I(D)) / I(t-p): this year's movement so
                         far, on the same base
                         -> carry_over + impulse = year on year, exactly
    change_in_yoy_pp     yoy(t) - yoy(t-1)
    this_period_pp       (I(t) - I(t-1)) / I(t-p): this period's movement
    base_effect_pp       I(t-1) (I(t-p) - I(t-p-1)) / (I(t-p) I(t-p-1)): the
                         movement a year ago leaving the comparison
                         -> this_period - base_effect = change in yoy, exactly
    """

    frame: pd.DataFrame
    periods_per_year: int
    notes: tuple[str, ...] = field(default_factory=tuple)


def base_effects(series: pd.Series, periods_per_year: int = 12) -> BaseEffects:
    """Split the year-on-year rate into carry-over and impulse, and its
    change into this period's movement and the base effect.

    The series must be regular -- one observation every period with none
    missing -- because "December of last year" and "a year ago" are
    positions in it; an irregular series is refused rather than silently
    compared with the wrong month.
    """
    _check_frequency(periods_per_year)
    s = pd.Series(series, dtype=float).dropna()
    index = pd.DatetimeIndex(s.index)
    step = 12 // periods_per_year
    gaps = (index.year[1:] - index.year[:-1]) * 12 + index.month[1:] - index.month[:-1]
    if len(index) and not (np.asarray(gaps) == step).all():
        raise DecompositionError(
            "base effects need a regular series with no missing periods; this one has gaps, "
            "so 'a year ago' and 'December last year' cannot be located by position")
    p = periods_per_year
    values = s.to_numpy(dtype=float)
    n = len(values)
    position = (index.month - 1) // step + 1          # 1..p within the calendar year
    out = {k: np.full(n, np.nan) for k in (
        "year_on_year_pct", "carry_over_pp", "impulse_pp", "change_in_yoy_pp",
        "this_period_pp", "base_effect_pp")}
    for t in range(p, n):
        year_ago = values[t - p]
        december = values[t - int(position[t])]
        out["year_on_year_pct"][t] = (values[t] / year_ago - 1.0) * 100.0
        out["carry_over_pp"][t] = (december - year_ago) / year_ago * 100.0
        out["impulse_pp"][t] = (values[t] - december) / year_ago * 100.0
        if t >= p + 1:
            before = values[t - p - 1]
            out["change_in_yoy_pp"][t] = (values[t] / year_ago - values[t - 1] / before) * 100.0
            out["this_period_pp"][t] = (values[t] - values[t - 1]) / year_ago * 100.0
            out["base_effect_pp"][t] = (values[t - 1] * (year_ago - before)
                                        / (year_ago * before)) * 100.0
    return BaseEffects(frame=pd.DataFrame(out, index=s.index), periods_per_year=p)


# ---------------------------------------------------------------------
# Diffusion and dispersion
# ---------------------------------------------------------------------
def diffusion(components: pd.DataFrame, weights: Mapping[str, float] | None = None, *,
              horizon: int = 1, threshold_pct: float | None = None,
              tolerance_pct: float = 0.0) -> pd.DataFrame:
    """How broad a price movement is.

    share_rising_pct / share_unchanged_pct / share_falling_pct
        of the components with a comparison, by count; "unchanged" is a
        change within `tolerance_pct` of zero, since a published index
        rounded to one decimal can show a tiny movement that is rounding
    diffusion_index
        share rising plus half the share unchanged: 50 is as many rising as
        falling, 100 is everything rising
    weighted_share_rising_pct
        the share of the basket, by effective weight, that is rising
    weighted_share_above_threshold_pct
        the share of the basket whose change exceeds `threshold_pct` -- "how
        much of the basket is rising faster than the target"
    """
    x = _changes(components, horizon)
    finite = x.notna()
    counted = finite.sum(axis=1).replace(0, np.nan)
    rising = (x > tolerance_pct) & finite
    falling = (x < -tolerance_pct) & finite
    unchanged = finite & ~rising & ~falling
    out = pd.DataFrame({
        "share_rising_pct": rising.sum(axis=1) / counted * 100.0,
        "share_unchanged_pct": unchanged.sum(axis=1) / counted * 100.0,
        "share_falling_pct": falling.sum(axis=1) / counted * 100.0,
    })
    out["diffusion_index"] = out["share_rising_pct"] + out["share_unchanged_pct"] / 2.0
    if weights is not None:
        columns = [c for c in components.columns if c in weights]
        ew = _effective_weights(components[columns], weights, horizon).where(finite[columns])
        total = ew.sum(axis=1).replace(0, np.nan)
        out["weighted_share_rising_pct"] = ew.where(rising[columns]).sum(axis=1) / total * 100.0
        if threshold_pct is not None:
            above = x[columns] > threshold_pct
            out["weighted_share_above_threshold_pct"] = (
                ew.where(above).sum(axis=1) / total * 100.0)
    out.loc[counted.isna()] = np.nan
    return out


def dispersion(components: pd.DataFrame, weights: Mapping[str, float] | None, *,
               horizon: int = 1) -> pd.DataFrame:
    """The weighted mean, spread and skewness of component price changes.

    mean_pct       the weighted mean change: the aggregate's own change
    dispersion_pp  sqrt( sum w (x - mean)^2 / sum w ): relative price
                   dispersion, the spread of relative price changes around
                   the aggregate
    skewness       sum w (x - mean)^3 / sum w / dispersion^3: positive when
                   a few large rises pull the mean above the typical change,
                   which is when a trimmed mean and the headline part company
    """
    w = _require_weights(components, weights, "dispersion")
    x = _changes(components[list(w)], horizon)
    ew = _effective_weights(components[list(w)], w, horizon).where(x.notna())
    total = ew.sum(axis=1).replace(0, np.nan)
    mean = (ew * x).sum(axis=1) / total
    deviation = x.sub(mean, axis=0)
    variance = (ew * deviation ** 2).sum(axis=1) / total
    sd = pd.Series(np.sqrt(variance), index=variance.index)
    third = (ew * deviation ** 3).sum(axis=1) / total
    return pd.DataFrame({
        "mean_pct": mean,
        "dispersion_pp": sd,
        "skewness": third / sd.where(sd > 0) ** 3,
    })


# ---------------------------------------------------------------------
# From a compiled run
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class Components:
    """Component indices, their weights and the tree above them, from
    wherever they came, with the reason when something is missing."""

    indices: pd.DataFrame
    weights: dict[str, float] | None
    parent_of: dict[str, str | None]
    root: str
    headline: pd.Series
    """The published (or compiled) headline over its full history, for the
    rates and base effects, which need more than the components' span."""
    panel: pd.DataFrame | None = None
    source: str = ""
    supplied_parent_weights: dict[str, float] | None = None
    notes: tuple[str, ...] = ()


def components_from_run(res: Mapping[str, Any]) -> Components:
    """The compiled run's categories as components of its "All items".

    The weights are the ones the run aggregated with (`category_weights`).
    Without them the run's headline is an equally weighted geometric mean,
    which has no exact additive decomposition, and the notes say so rather
    than decomposing a different aggregate under the headline's name.
    """
    from .index import category_weights

    indices: pd.DataFrame = res["indices"]
    panel: pd.DataFrame | None = res.get("imputed")
    categories = ([str(c) for c in panel["category"].dropna().unique()]
                  if panel is not None and "category" in panel.columns else
                  [c for c in indices.columns if c != "All items"])
    leaves = [c for c in indices.columns if c in set(categories)]
    weights = category_weights(panel) if panel is not None else None
    notes: list[str] = []
    if weights is None:
        notes.append(
            "this run has no expenditure weights, so its headline is an equally weighted "
            "geometric mean of the categories. A geometric mean has no exact additive "
            "decomposition, and contributions and the weighted core measures are not "
            "computed rather than computed for a different aggregate than the one published")
    parent_of: dict[str, str | None] = {c: "All items" for c in leaves}
    parent_of["All items"] = None
    return Components(indices=indices[leaves], weights=weights, parent_of=parent_of,
                      root="All items", headline=indices["All items"], panel=panel,
                      source="the compiled run", notes=tuple(notes))
