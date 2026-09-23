# Spatial price comparison (`engine/spatial.py`)

## What the module computes

Purchasing power parities between regions — local currency per unit of a
base region's currency, base = 1 — by two methods, and conversion of
local-currency values at the published parities.

**Country product dummy (CPD).** A regression of log prices on region and
product dummies:

    ln p_rn = α_r + β_n + ε_rn,        PPP_r = exp(α_r − α_base)

optionally weighted by each product's share of the region's expenditure
(weighted CPD). It handles any pattern of missing prices provided the
regions are connected through shared products, and gives standard errors
when there are more prices than parameters.

**Geary-Khamis.** International prices and parities defined jointly,

    π_n   = Σ_r (p_rn q_rn / PPP_r) / Σ_r q_rn
    PPP_r = Σ_n p_rn q_rn / Σ_n π_n q_rn

solved by iteration from PPP = 1 and normalised to the base. Quantity
weighted, so large regions dominate the international prices (the
Gerschenkron effect), and additive in real expenditure, which is why it is
the traditional choice for aggregate volume comparisons.

The tests check both on a case with a known solution — every price exactly
PPP_r × π_n, with gaps and free quantities — which both recover to 1e-9, and
on a two-region, two-product case solved by hand, where they differ (3 for
Geary-Khamis, √8 for CPD) because one weights by quantity and the other by
count.

## Thin overlap is reported, not published

A parity resting on a handful of shared products is not a measurement, for
the same reason a thin index stratum is not. Every result carries the
matched-product count for every pair of regions (`overlap`), lists every pair
below the threshold (`thin_pairs`), and **withholds** a region whose best
pairing with any other region shares fewer than `min_overlap` products
(five by default): its estimate is shown with the count and the reason, and
it is left out of the published parities and of any conversion built on
them. A region that shares no chain of products with the base at all has no
identified parity by any method: it is reported with that reason and the
rest of the comparison goes ahead without it.

## Real data

`tests/test_spatial_eurostat.py` runs a weighted CPD on Eurostat's
purchasing power parities by analytical category for 2023, fetched through
the Eurostat connector (`data/connectors/eurostat.ppp_comparison_inputs`):
each country's most detailed published categories under actual individual
consumption, their PPPs as prices and their national-currency expenditure as
weights, against the EU27 base.

The real data's raggedness was not the thin overlap constructed data had
produced but *disconnection*: Japan, the United States and the United
Kingdom publish only aggregates, so they share no detailed category with
anyone. The engine used to refuse the whole comparison in that case; it now
withholds such regions with the reason. The 36 connected countries share 15
categories each — 23 are priced, but expenditure is published for only 15.

Against Eurostat's published PPPs for actual individual consumption the
result is a median 5.5% away, and the gap is systematic rather than noise:
the 15 categories cover a median 49% of each country's consumption and leave
out rents, most health and education and government-provided services —
the non-traded services cheapest in low-price countries — so the cheaper
the country, the more the result overstates its price level (Spearman
correlation of −0.77 between the gap and Eurostat's price level index).
Eurostat itself aggregates basic-heading parities -- far more detailed than
these categories -- by the EKS method, not a handful of categories by CPD.

## Citation

World Bank, *Purchasing Power Parities and the Real Size of World
Economies: A Comprehensive Report of the 2011 International Comparison
Program* and the ICP Book (*Measuring the Real Size of the World Economy*,
2013), chapters on the CPD and on aggregation methods. Summers (1973) for
CPD; Geary (1958) and Khamis (1972) for Geary-Khamis. Eurostat-OECD
*Methodological Manual on Purchasing Power Parities* (2012).

## What the module does not do

No GEKS or EKS aggregation of bilateral parities, no linking of regional
comparisons across time, no representativity (CPRD) adjustment, and no
basic-heading structure: one level of products is compared. Standard errors
are for CPD only.
