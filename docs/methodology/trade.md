# Trade price indices (`engine/trade.py`)

## What the module computes

Import and export price indices, unit value indices, and the terms of trade,
from customs-style transactions (period, flow, product, value, quantity).

**Price index.** Each product's price is its own unit value — value over
quantity — at the most detailed product level supplied, and products are
aggregated by a Fisher (default), Laspeyres or Paasche formula from
`engine/bilateral.py`, direct against the base period over the products
traded in both periods. The composition of trade enters as weights, not as
price.

**Unit value index.** Total value over total quantity per period, against
the base. **Terms of trade.** Export price index over import price index,
times 100; both must be price indices on the same base, and the result is
their ratio exactly (tested to zero tolerance).

## The unit value bias

A unit value is not a price. When the mix of what is traded shifts, the
unit value moves although no price has changed. The test that justifies
the module, `tests/test_trade.py::test_the_unit_value_bias_by_hand`:

| | cheap (10 a tonne) | dear (100 a tonne) | value | tonnes | unit value |
|---|---|---|---|---|---|
| January | 90 t | 10 t | 1,900 | 100 | 19 |
| February | 10 t | 90 t | 9,100 | 100 | 91 |

Neither price changes, so every price index formula gives 100. The unit
value index gives 91/19 × 100 = **478.95**: a gap of **378.95 index points**,
a composition effect of 4.79, all of it the shift in mix.

Every unit value index the module returns carries `UNIT_VALUE_WARNING` and
`UNIT_VALUE_CONDITIONS` on the result itself; the page prints them before any
unit value number and puts them on the first line of the download. The
conditions under which a unit value is a defensible stand-in for a price:
the products pooled in each cell are homogeneous (one detailed commodity,
such as an 8- or 10-digit HS code, of stable quality), their mix within the
cell is stable from period to period, and their quantities are in one
physical unit.

## Citation

IMF, *Export and Import Price Index Manual: Theory and Practice* (2009),
chapters on unit value indices and their bias, and on the terms of trade.
Silver, "Do Unit Value Export, Import, and Terms of Trade Indices Represent or
Misrepresent Price Indices?", IMF Working Paper 07/121 (2007).

## What the module does not do

No survey-price collection, no quality adjustment of traded products, no
chaining (the indices are direct against the base), and no seasonal or
outlier treatment of customs records; a product's unit value is taken at
the detail supplied, and its homogeneity is the user's to ensure.
