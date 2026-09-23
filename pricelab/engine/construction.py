"""Construction price indices: the input cost index and the output price
index.

They answer different questions and are routinely confused, so the
difference is stated here, on every result (`ConstructionIndex.concept`),
and on the page before either number.

Input cost index
    What it costs a builder to buy the inputs of construction -- materials,
    labour, plant hire, energy -- at fixed shares of cost. A weighted
    average of input price indices. It does not see what the builder does
    with the inputs: a contractor who works faster, or cuts a margin to win
    work, leaves it unchanged. It is the index for escalating a contract's
    cost of inputs, and for a builder's own costs.

Output price index
    What a client pays for a finished piece of construction of fixed
    specification. It includes the contractor's margins and overheads and
    reflects productivity: if builders get more efficient, or competition
    for tenders tightens, output prices rise less than input costs. It is
    the index for deflating construction output, and for what building has
    cost the buyer. Here it is compiled by pricing a fixed bill of
    quantities at each period's tender rates -- the specification is held
    fixed, so a change in what is built is not mistaken for a change in
    price.

The ratio of the two (`input_output_gap`) is therefore not an error term:
it is the implied movement in margins and productivity together, and it
is reported as that.

Sources: OECD and Eurostat, *Construction Price Indices: Sources and Methods*
(1997); Eurostat, *Methodological Manual
for Short-term Business Statistics*, construction costs and output prices.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import pandas as pd

__all__ = [
    "CONCEPTS",
    "ConstructionError",
    "ConstructionIndex",
    "input_cost_index",
    "input_output_gap",
    "output_price_index",
]

CONCEPTS: dict[str, str] = {
    "input_cost": (
        "Input cost index: what it costs a builder to buy the inputs of construction "
        "(materials, labour, plant, energy) at fixed cost shares. It does not reflect "
        "productivity, margins or overheads, so it answers 'what are my inputs costing me?', "
        "not 'what does a finished building cost?'."),
    "output_price": (
        "Output price index: what a client pays for finished construction of fixed "
        "specification, including the contractor's margins and overheads and the effect of "
        "productivity. It answers 'what does the finished work cost the buyer?' and is the "
        "one to deflate construction output with."),
}


class ConstructionError(ValueError):
    """The inputs cannot support the index asked of them."""


@dataclass(frozen=True)
class ConstructionIndex:
    kind: str
    """"input_cost" or "output_price"."""
    index: pd.Series
    base: pd.Timestamp
    weights: pd.Series
    """Cost shares (input cost) or bill-of-quantities values at base-period
    rates (output price), normalised to sum to one."""
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def concept(self) -> str:
        return CONCEPTS[self.kind]

    @property
    def label(self) -> str:
        name = "Construction input cost index" if self.kind == "input_cost" else \
            "Construction output price index"
        return f"{name}, {self.base:%b %Y} = 100. {self.concept}"


def _base_of(index: pd.DatetimeIndex, base: pd.Timestamp | None) -> pd.Timestamp:
    chosen = pd.Timestamp(base) if base is not None else pd.Timestamp(index.min())
    if chosen not in index:
        raise ConstructionError(f"the base period {chosen:%Y-%m} is not in the data")
    return chosen


def input_cost_index(input_indices: pd.DataFrame, cost_shares: Mapping[str, float], *,
                     base: pd.Timestamp | None = None) -> ConstructionIndex:
    """Laspeyres-type: sum_i s_i I_i(t) / I_i(base), times 100.

    `input_indices` has one column per input (any reference period; each is
    rebased to the base period here) and `cost_shares` the share of each in
    the cost of the reference project. Every input must have a share, and
    every share an input: a cost share with no price index behind it would
    be silently held flat.
    """
    frame = input_indices.sort_index().astype(float)
    frame.index = pd.DatetimeIndex(frame.index)
    missing = sorted(set(cost_shares) - set(frame.columns))
    unweighted = sorted(set(frame.columns) - set(cost_shares))
    if missing or unweighted:
        raise ConstructionError(
            f"inputs and cost shares must match: shares with no index {missing}, indices with "
            f"no share {unweighted}")
    shares = pd.Series({k: float(v) for k, v in cost_shares.items()})
    if (shares < 0).any() or shares.sum() <= 0:
        raise ConstructionError("cost shares must be non-negative and not all zero")
    shares = shares / shares.sum()
    b = _base_of(pd.DatetimeIndex(frame.index), base)
    relatives = frame[list(shares.index)] / frame.loc[b, list(shares.index)]
    index = (relatives * shares).sum(axis=1, min_count=len(shares)) * 100.0
    return ConstructionIndex(kind="input_cost", index=index.rename("input cost index"), base=b,
                             weights=shares)


def output_price_index(tender_rates: pd.DataFrame, bill_of_quantities: Mapping[str, float], *,
                       base: pd.Timestamp | None = None) -> ConstructionIndex:
    """The fixed bill of quantities priced at each period's tender rates:

        sum_k q_k r_k(t) / sum_k q_k r_k(base), times 100

    `tender_rates` is a long table with `period`, `item` and `rate` (the
    price per unit of each bill item, from tenders or contractors' quotes);
    `bill_of_quantities` the quantity of each item in the reference
    project. The specification never changes, which is the point: a
    period in which an item has no rate cannot be priced, and is left out
    with a note rather than priced without that item.
    """
    needed = {"period", "item", "rate"}
    missing_cols = sorted(needed - set(tender_rates.columns))
    if missing_cols:
        raise ConstructionError(f"the tender rates have no {missing_cols} column(s)")
    wide = tender_rates.pivot_table(index="period", columns="item", values="rate",
                                    aggfunc="mean").sort_index()
    wide.index = pd.DatetimeIndex(wide.index)
    items = list(bill_of_quantities)
    unpriced = sorted(set(items) - set(wide.columns))
    if unpriced:
        raise ConstructionError(f"bill items with no tender rate in any period: {unpriced}")
    quantities = pd.Series({k: float(v) for k, v in bill_of_quantities.items()})
    b = _base_of(pd.DatetimeIndex(wide.index), base)
    if wide.loc[b, items].isna().any():
        raise ConstructionError(f"the base period {b:%Y-%m} does not price every bill item")
    cost = (wide[items] * quantities).sum(axis=1, min_count=len(items))
    incomplete = [f"{p:%Y-%m}" for p in cost.index[cost.isna()]]
    notes = ((f"{len(incomplete)} period(s) lack a rate for at least one bill item and are not "
              f"priced: {', '.join(incomplete[:6])}",) if incomplete else ())
    base_values = pd.Series(wide.loc[b, items], dtype=float) * quantities
    return ConstructionIndex(
        kind="output_price", index=(cost / float(cost[b]) * 100.0).rename("output price index"),
        base=b, weights=base_values / base_values.sum(), notes=notes)


def input_output_gap(inputs: ConstructionIndex, outputs: ConstructionIndex) -> pd.DataFrame:
    """Output prices over input costs, both on the same base.

    Above 100: clients are paying more than inputs cost the builder more --
    margins widening, or productivity falling. Below 100: the reverse.
    Neither index is wrong when they diverge; they measure different
    things, and this is the measure of how different.
    """
    if inputs.kind != "input_cost" or outputs.kind != "output_price":
        raise ConstructionError("pass an input cost index and then an output price index")
    if inputs.base != outputs.base:
        raise ConstructionError("the two indices must share a base period")
    common = inputs.index.index.intersection(outputs.index.index)
    frame = pd.DataFrame({"input_cost_index": inputs.index[common],
                          "output_price_index": outputs.index[common]})
    frame["output_over_input"] = frame["output_price_index"] / frame["input_cost_index"] * 100.0
    return frame
