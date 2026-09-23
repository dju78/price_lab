"""Trade price indices: import and export price indices, unit value
indices, and the terms of trade.

The unit value bias
-------------------
Customs records give a value and a quantity for each commodity shipped, so
the cheapest index to build is a unit value index: total value over total
quantity, compared across periods. It is not a price index. When the mix of
what is traded shifts -- more of the expensive product, less of the cheap
one -- the unit value moves although no price has changed, and the index
reports the shift in composition as price change.
`unit_value_bias` measures exactly how much, and
`tests/test_trade.py` shows it on data where every price is unchanged and
the unit value index nevertheless rises 379 points.

A unit value is a defensible stand-in for a price only under conditions
that `UNIT_VALUE_CONDITIONS` states, and every unit value index this module
returns carries that statement (`TradeIndexResult.warning`), so no screen or
file can show one without it.

The price indices
-----------------
`price_index` treats each product's own unit value (value over quantity at
the most detailed product level supplied) as its price and aggregates the
products with a Fisher, Laspeyres or Paasche formula from
`engine/bilateral.py` -- so the composition of trade enters as weights, not
as price. How good that is depends on how homogeneous each product is,
which is the same condition as for a unit value, applied at the detailed
level where it is most likely to hold. Survey prices, where an office
collects them, go in the same `price` role.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import bilateral

__all__ = [
    "FLOWS",
    "TradeError",
    "TradeIndexResult",
    "UNIT_VALUE_CONDITIONS",
    "UNIT_VALUE_WARNING",
    "price_index",
    "terms_of_trade",
    "unit_value_bias",
    "unit_value_index",
]

FLOWS = ("export", "import")
FORMULAS = ("fisher", "laspeyres", "paasche")

UNIT_VALUE_WARNING = (
    "A unit value index is total value divided by total quantity, compared across periods. "
    "It moves when the composition of trade shifts even if no price changes, so it reports "
    "shifts in product mix and quality as price change (the unit value bias).")

UNIT_VALUE_CONDITIONS = (
    "Using it as a price index is defensible only when the products pooled in each cell are "
    "homogeneous (one detailed commodity, such as an 8- or 10-digit HS code, of stable "
    "quality), their mix within the cell is stable from period to period, and their "
    "quantities are in one physical unit. Where those conditions fail, use a price index over "
    "detailed products or survey prices instead.")


class TradeError(ValueError):
    """The transactions cannot support the index asked of them."""


@dataclass(frozen=True)
class TradeIndexResult:
    flow: str
    kind: str
    """"price" or "unit value"."""
    index: pd.Series
    """Base period = 100."""
    base: pd.Timestamp
    formula: str
    products: pd.Series
    """Products entering the comparison with the base, per period."""
    warning: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        what = (f"{self.flow} price index, {self.formula}" if self.kind == "price"
                else f"{self.flow} unit value index")
        text = f"{what}, {self.base:%b %Y} = 100"
        return f"{text}. {self.warning}" if self.warning else text


def _prepare(transactions: pd.DataFrame, flow: str) -> pd.DataFrame:
    if flow not in FLOWS:
        raise TradeError(f"flow must be one of {FLOWS}, not {flow!r}")
    needed = {"period", "flow", "product", "value", "quantity"}
    missing = sorted(needed - set(transactions.columns))
    if missing:
        raise TradeError(f"the transactions have no {missing} column(s)")
    frame = transactions[transactions["flow"].astype(str).str.lower() == flow].copy()
    if frame.empty:
        raise TradeError(f"there are no {flow} transactions")
    frame["period"] = pd.DatetimeIndex(frame["period"])
    frame = frame.dropna(subset=["value", "quantity"])
    if ((frame["value"] < 0) | (frame["quantity"] <= 0)).any():
        raise TradeError("values must be non-negative and quantities positive")
    # One row per product and period: several shipments of the same product
    # are summed, so the product's unit value is total value over total
    # quantity for that period.
    grouped = frame.groupby(["period", "product"], as_index=False)[["value", "quantity"]].sum()
    grouped["price"] = grouped["value"] / grouped["quantity"]
    return grouped


def _base(frame: pd.DataFrame, base: pd.Timestamp | None) -> pd.Timestamp:
    periods = sorted(frame["period"].unique())
    chosen = pd.Timestamp(base) if base is not None else pd.Timestamp(periods[0])
    if chosen not in set(pd.DatetimeIndex(periods)):
        raise TradeError(f"the base period {chosen:%Y-%m} has no transactions")
    return chosen


def price_index(transactions: pd.DataFrame, flow: str, *, formula: str = "fisher",
                base: pd.Timestamp | None = None) -> TradeIndexResult:
    """A direct price index for one flow, each period against the base,
    over the products traded in both.

    Each product's price is its own unit value at the detail supplied; the
    products are aggregated by `formula`, with quantities as the weights --
    base-period quantities for Laspeyres, current for Paasche, both for
    Fisher. Products traded in only one of the two periods cannot be
    compared and are counted out, which `products` shows per period.
    """
    if formula not in FORMULAS:
        raise TradeError(f"formula must be one of {FORMULAS}, not {formula!r}")
    frame = _prepare(transactions, flow)
    b = _base(frame, base)
    wide_p = frame.pivot(index="product", columns="period", values="price")
    wide_q = frame.pivot(index="product", columns="period", values="quantity")
    values: dict[pd.Timestamp, float] = {}
    counts: dict[pd.Timestamp, int] = {}
    for period in wide_p.columns:
        t = pd.Timestamp(period)
        p0, pt, q0, qt = wide_p[b], wide_p[period], wide_q[b], wide_q[period]
        if formula == "fisher":
            result = bilateral.fisher(p0, pt, q0, qt)
        elif formula == "laspeyres":
            result = bilateral.laspeyres(p0, pt, q0)
        else:
            result = bilateral.paasche(p0, pt, qt)
        values[t] = result.value * 100.0
        counts[t] = result.n_items
    return TradeIndexResult(flow=flow, kind="price", index=pd.Series(values).sort_index(),
                            base=b, formula=formula, products=pd.Series(counts).sort_index())


def unit_value_index(transactions: pd.DataFrame, flow: str, *,
                     base: pd.Timestamp | None = None) -> TradeIndexResult:
    """Total value over total quantity per period, against the base.

    Returned with `UNIT_VALUE_WARNING` and `UNIT_VALUE_CONDITIONS` attached,
    always: there is no way to obtain a unit value index from this module
    without the statement of what it measures.
    """
    frame = _prepare(transactions, flow)
    b = _base(frame, base)
    totals = frame.groupby("period")[["value", "quantity"]].sum()
    unit_value = totals["value"] / totals["quantity"]
    units = frame.groupby("period")["product"].nunique()
    return TradeIndexResult(
        flow=flow, kind="unit value", index=(unit_value / float(unit_value[b]) * 100.0),
        base=b, formula="unit value", products=units,
        warning=f"{UNIT_VALUE_WARNING} {UNIT_VALUE_CONDITIONS}")


@dataclass(frozen=True)
class UnitValueBias:
    """The unit value index against the price index on the same data."""

    unit_value: TradeIndexResult
    price: TradeIndexResult
    table: pd.DataFrame
    """Per period: both indices, the gap in index points, and the
    composition effect -- the unit value index divided by the price index,
    which is what the change in mix alone did to the unit value."""
    max_gap_points: float
    warning: str = f"{UNIT_VALUE_WARNING} {UNIT_VALUE_CONDITIONS}"


def unit_value_bias(transactions: pd.DataFrame, flow: str, *, formula: str = "fisher",
                    base: pd.Timestamp | None = None) -> UnitValueBias:
    """How far the unit value index departs from the price index.

        composition effect = unit value index / price index

    With the Fisher price index this is, up to the products counted out of
    the matched comparison, the Fisher quantity index of the products
    divided by the change in total physical quantity: the part of the unit
    value's movement that is the mix, not the prices.
    """
    uv = unit_value_index(transactions, flow, base=base)
    pr = price_index(transactions, flow, formula=formula, base=base)
    table = pd.DataFrame({"unit_value_index": uv.index, "price_index": pr.index})
    table["gap_points"] = table["unit_value_index"] - table["price_index"]
    table["composition_effect"] = table["unit_value_index"] / table["price_index"]
    gap = float(table["gap_points"].abs().max())
    return UnitValueBias(unit_value=uv, price=pr, table=table,
                         max_gap_points=gap if np.isfinite(gap) else float("nan"))


def terms_of_trade(exports: TradeIndexResult, imports: TradeIndexResult) -> pd.Series:
    """Export prices over import prices, times 100.

    Both indices must be of the same kind and share a base period, or the
    ratio compares things that do not line up; a period missing from
    either is left out rather than filled.
    """
    if exports.flow != "export" or imports.flow != "import":
        raise TradeError("terms of trade need an export index and an import index, in that order")
    if exports.base != imports.base:
        raise TradeError(
            f"the export index is based on {exports.base:%b %Y} and the import index on "
            f"{imports.base:%b %Y}; rebase one of them first")
    if exports.kind != imports.kind:
        raise TradeError("do not divide a unit value index by a price index")
    common = exports.index.index.intersection(imports.index.index)
    return (exports.index[common] / imports.index[common] * 100.0).rename("terms of trade")
