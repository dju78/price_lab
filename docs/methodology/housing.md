# Rents and owner-occupied housing (`engine/housing.py`)

## Rents

`rent_index`: in each stratum, the geometric mean of the same dwellings' rent
relatives between consecutive periods, chained, and strata combined with
fixed weights (the base period's total rent by default). What it measures:
the change in rent for the same dwellings. Its limitation, stated on the
result: it follows sitting tenancies as much as new lets, which move
differently, and a dwelling ageing in the sample carries its depreciation
into the index unless adjusted for.

## Owner-occupied housing: four questions

| Approach | The question it answers | Counts | Leaves out |
|---|---|---|---|
| Rental equivalence | What would owner-occupiers pay to rent the homes they live in? | the housing service, at market rents | the asset |
| Net acquisitions | What do households pay to acquire dwellings new to the household sector? | new dwellings bought by households | the existing stock changing hands; the land, in the HICP's treatment |
| User cost | What does it cost, each period, to own a home? | forgone return, depreciation, maintenance, taxes, less the expected capital gain | nothing about ownership — which is why it can go negative |
| Payments | What do owner-occupiers actually pay out? | mortgage interest, repairs, insurance, property taxes | the owner's own capital; mortgage principal, which is saving |

They are not four estimators of one quantity, and the page does not offer a
choice between them: it states the four questions side by side, then gives
each approach its own section under its own question. Every result's label
names the question it answers.

**Net acquisitions** can exclude land: structure(t) = [P(t) − s L(t)] / (1 − s),
with the land share s and a land price index L, so a land boom does not
reach a consumer price index. **User cost**: V(t) × [i(t) + d + m + tax − g(t)],
with the expected gain g a backward-looking average of annual house price
growth over a stated number of years; the realised-gain version is carried
beside it, because the result is highly sensitive to the expectation. When
expected gains exceed the rest the cost is negative, and it is reported, not
floored. **Payments** refuses a column that looks like capital repayment.

## Citation

CPI Manual 2020, the chapter on owner-occupied housing; Eurostat, *Technical
manual on Owner-Occupied Housing and House Price Indices* (2017); Diewert,
"The Treatment of Owner Occupied Housing and Other Durables in a Consumer
Price Index", in Diewert, Greenlees and Hulten (eds.), *Price Index Concepts
and Measurement* (2009).

## What the module does not do

No quality or age adjustment of rents, no imputed-rent weights, no
opportunity-cost variant of user cost with a separate rate of return on
equity, and no weighting of the approaches into a consumer price index.
