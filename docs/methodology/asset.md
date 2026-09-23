# Residential property price indices (`engine/asset.py`)

## Five families, five questions

| Method | Measures | Rests on |
|---|---|---|
| Stratified median | the median sale price per stratum, at a fixed mix of strata | the strata: a shift in what sells *within* a stratum is reported as price |
| Mix-adjusted mean | the mean price per cell (stratum × size band), at a fixed mix of cells | the cells: quality change within a cell is reported as price |
| Repeat sales (Bailey-Muth-Nourse; Case-Shiller weighted) | the price change of the same properties between their sales | properties that sold twice (a selected sample); renovation and depreciation treated as no change; the whole history revised every period |
| Sale price appraisal ratio (SPAR) | sale prices against a fixed appraisal of the same properties | the appraisal: its date, its quality, its even-handedness |
| Hedonic (time dummy) | the price of a dwelling of fixed characteristics | the specification: a characteristic that matters but is missing leaves its quality change in the index |

These are routinely misread as disagreeing estimates of one number. Every
result carries what it measures and its limitation (`label`), and the page
prints both beside each number.

**Stratified median** and **mix-adjusted mean** combine their strata or cells
with the base period's value of sales, renormalising over what sold in a
period and saying so when they must. **Repeat sales**: for each pair of
consecutive sales of one property, ln(P2/P1) = β(t2) − β(t1) + ε, β(base) = 0,
by least squares; Case-Shiller models the squared residuals as a + b × months
between sales and re-weights the pairs by the reciprocal. The geometric form
is implemented; the arithmetic, value-weighted form estimated by
instrumental variables is not. **SPAR** is the ratio of sums, Σ price / Σ
appraisal against the same ratio in the base period. **Hedonic** is
`engine/hedonic.fit_hedonic` with time dummies — the estimator quality
adjustment uses, not a second implementation.

## Why they differ, in numbers

`compare_methods` runs all of them on the same sales and explains the gaps.
It values each period's sold properties at the base period's hedonic
characteristic prices: the change in that value is the change in the
*quality mix* of what sold. It reports the mix change within strata and
within cells — the part each group-based method cannot see — beside that
method's gap to the hedonic index. On the demonstration market, where larger
homes sell increasingly often, the quality of what sold rises 6.86%; within
strata, 5.93% (about 7 of the stratified median's 12.9 points above the
hedonic index, the rest being the median's sensitivity to the middle of each
stratum); within cells, −1.41% (about −1.7 points, against the mix-adjusted
mean's actual −1.3). The explanation also states how many pairs repeat sales
rests on and what share of all sales they are.

## Revisions

A repeat sales index re-estimates its whole history every time a period is
added. `repeat_sales_vintages` estimates the index as it would have been
published at the end of each period, as `engine/revision.Vintage` objects,
and `repeat_sales_revisions` passes them to `engine/revision.analyse` — the
same triangle, mean and mean absolute revision and bias test as registry
corrections. The Revisions page shows them in the same frame. On the
demonstration market the mean absolute revision is 0.42 index points.

## Diagnostics and suppression

`transaction_counts` gives sales per stratum per period. `market_coverage`
gives sales per stratum per period, the share of a dwelling stock sold where
one is supplied, and the share of sales each method can use (all, for the
median and mean; the second sales of pairs, for repeat sales; those with an
appraisal, for SPAR; those with every characteristic, for the hedonic).
`suppress_strata` puts the stratified median's per-stratum table through
`core.security.suppress_with_secondary`: a stratum-period below the minimum
count is suppressed, and in any period with one such suppression the
smallest remaining stratum is suppressed too, so the first cannot be backed
out of the published all-strata index.

## Real data

Eurostat publishes house price indices, not transactions, so the five
transaction methods cannot be run on agency data through an existing
connector. What can be checked is the aggregation: each country's published
total is an annually chain-linked aggregate of its new- and
existing-dwelling indices with published weights. Rebuilding it with
`engine/decomposition.chain_linked_aggregate` at a fourth-quarter link
reproduces the published total to within 0.061 index points for Germany and
under 0.02 for Ireland, the Netherlands, France, Spain, Denmark, Poland and
the EU (2020 Q1 to 2025 Q2), the gap being the rounding of the two-decimal
published parts.

## Real transactions: HM Land Registry price paid data

The five methods were run on every residential sale in England and Wales in
2024 (`data/price_paid.py`; 930,559 records, downloaded from HM Land
Registry under the Open Government Licence). `tests/test_price_paid.py`
holds each fix on a verbatim extract for Oldham (3,108 records).

What the real data did that constructed data had not:

| Finding | What was done |
|---|---|
| No header row | the loader took the first transaction as column names; it now recognises a headerless file (a full date and a number in the same columns of the first two rows) |
| Every field quoted | the loader's fixed-size sample ended inside a quoted field and the whole upload failed ("EOF inside string"); the sample is now cut to its last complete line |
| 18% category B (repossessions, buy-to-let, transfers to companies), including every "other" property | excluded, as the UK HPI excludes them |
| prices from GBP 1 to 180 million | outside GBP 10,000–5,000,000 excluded (517 market sales, once category B is gone) |
| the same property, date and price twice | the repeat dropped (1,166) |
| no postcode | kept, but out of repeat sales (164) |
| no floor area, rooms or appraisal | hedonic on type, tenure, new build and county; mix-adjusted cells by type, tenure and new build; no sale price appraisal ratio |
| re-sales within a year are mostly not price change: a third on the same day, median gap 44 days, ratios from 0.05 to 19.6 | pairs under six months apart, pairs whose first sale was a new build and pairs moving by more than a factor of two excluded (`PairRules`) |
| a district-year may hold two usable repeat sales pairs | repeat sales is reported as not computed, with the reason, and the other methods still run |
| a dummy per county made the hedonic diagnostics run for over ten minutes without finishing | VIF from the inverse correlation matrix and leverage by one matrix product: the same numbers, a 63-second fit |

On the 761,650 sales that remain, December 2024 against January 2024:
hedonic **102.73**, stratified median **102.46**, mix-adjusted mean
**99.21**, repeat sales **125.75** (Bailey-Muth-Nourse) and **125.84**
(Case-Shiller). The first three agree to within 3.5 points, and the
explanation accounts for the mix-adjusted mean's gap only in part: its
cells cannot see location, so a shift in where sales happen passes into it.
Repeat sales is 23 points above them, on 649 pairs (0.1% of sales), because a
single year's pairs are selected on quick resales; the most plausible reading
(not verified property by property) is homes bought, refurbished and sold
again within months, whose price change is renovation as much as market. It is the selected-sample limitation stated beside every repeat
sales number, made visible; a single year cannot fix it, and several years
of files would be needed for a repeat sales index worth publishing. Its
history revised by a mean absolute 1.66 index points as each month was
added. The hedonic fit flags severe collinearity on the county dummies,
because the reference county is small; the fitted index is unaffected, and
the warning is left in rather than suppressed.

Chosen not to fix: leasehold houses (a majority of sales in some north-west
districts) are kept and used as a characteristic rather than modelled as a
separate market; the price bounds are fixed amounts rather than a statistical
rule; and a single year's file is used, as the task specified.

## Citation

Eurostat, *Handbook on Residential Property Prices Indices (RPPIs)* (2013).
Bailey, Muth and Nourse, "A Regression Method for Real Estate Price Index
Construction", *Journal of the American Statistical Association* (1963).
Case and Shiller, "Prices of Single-Family Homes since 1970", *New England
Economic Review* (1987).

## What the module does not do

The arithmetic Case-Shiller form; depreciation or renovation adjustment of
repeat sales; hedonic imputation (double imputation) indices; geographic
(spatial) hedonic terms; and land and structure decomposition.
