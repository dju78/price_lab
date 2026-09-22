# Multilateral methods and window extension (`engine/multilateral.py`)

## What the module computes

A multilateral index estimates all `T` periods of a window simultaneously,
against the whole window rather than against each other in sequence. The
result is **transitive**: the comparison between any two periods in the window
does not depend on which periods lie between them, so there is no path along
which chain drift can accumulate. That is the entire reason to reach for one,
and it is only worth reaching for on data where a chained bilateral index
actually drifts — transaction and scanner data, where products churn
continuously, prices bounce between shelf and promotion, and quantities
respond violently to the bounce.

| Method | Definition | Uses unmatched observations? |
|---|---|---|
| GEKS-Fisher | `P(s,t) = Π_l [P_F(l,t) / P_F(l,s)]^(1/L)` over bridge periods `l` | No |
| GEKS-Törnqvist (CCDI) | the same with `P_T` as the block | No |
| Time product dummy (TPD) | `ln p_it = α + δ_t + γ_i + ε_it`, index `exp(δ_t)` | Yes |
| Weighted TPD | the same, weighted by each observation's share of its own period's expenditure | Yes |
| Time dummy hedonic (TDH) | the same with characteristics in place of `γ_i`, fitted by `engine/hedonic.py` | Yes, including products never seen before |
| Geary-Khamis | `b_i = Σ_t [q_it / Σ_s q_is] · p_it / P_t` and `P_t = Σ_i p_it q_it / Σ_i b_i q_it`, iterated | Partially: a product needs one period, not a matched pair |

In logs, GEKS collapses to one level per period — `g(t) = (1/L) Σ_l ln P(l,t)`,
with `ln P_GEKS(s,t) = g(t) − g(s)` — which is where transitivity comes from
and how the engine computes it. Every bilateral block used satisfies time
reversal exactly, so the matrix of log comparisons is built antisymmetric by
construction and the result is transitive to machine precision rather than to
within rounding.

## Extension: publishing from a window that moves

A multilateral index is transitive *within its window*, and the window has to
move. Adding a period re-estimates every level in the new window, and a
published consumer price index cannot be revised, so an office must decide how
much of the re-estimation to accept. The six rules differ only in which period
of the overlap the new window is linked at:

| Rule | Links at | Admits |
|---|---|---|
| Movement splice | the immediately preceding period | the newest movement only |
| Window splice | the far end of the overlap | the whole window's re-estimation |
| Half splice | half a window back | the compromise the Eurostat guide recommends absent local evidence |
| Mean splice | every period of the overlap, geometrically averaged | no single month can dominate the link |
| FBEW | the anchor period, from a window expanding from it | no splicing within a year; one link a year |
| FBMW | the previous period, from that same expanding window | a short within-year chain over a transitive window |

The default window is **25 monthly periods**: two full years plus the month
being added, so every seasonal month is observed twice and a seasonal product
is never compared only against its own high season. The default rule is the
**mean splice**, because it is the least exposed to one unrepresentative month
and the default is applied to data nobody has looked at yet.

`splice_spread_pp` reports, per published period, the gap between the highest
and lowest level that period could have taken had the link been made
elsewhere in the overlap. That is the size of the judgement the rule made. A
"revision against the previous window" measured at the splice point is zero by
construction for whichever rule was used, so it would flatter every choice
equally and is deliberately not what is reported.

## Citation

CPI Manual 2020, Chapter 7: GEKS paras 7.137–7.146, Geary-Khamis paras
7.147–7.163, the time product dummy paras 7.164–7.172; Chapter 10, paras
10.26–10.45 on scanner data and chain drift, and Table 10.1's worked example
of a chained Törnqvist ending 22 percent below the direct index on data where
every price and quantity returned to its starting value. Eurostat, *Guide on
Multilateral Methods in the HICP* (2022) for the extension rules and their
names. Chessa (2016) for FBEW as implemented by Statistics Netherlands. De
Haan and Krsinich (2018) for the weighted time product dummy. Caves,
Christensen and Diewert (1982) and Inklaar and Timmer for CCDI.

## Assumptions

**GEKS** assumes only that its bilateral building block is a sensible
comparison, but it is matched-model: a product must be sold in *both* periods
of a pair to enter that pair. On a window where products turn over fast this
discards the very transactions that motivated using the data.
`diagnostics["matched_expenditure_share"]` reports how much of the window's
expenditure a strictly matched comparison would keep; where that is low, the
answer is TPD or TDH, not a different splice.

GEKS also needs the window to be **connected**: a period that cannot be
compared to every other cannot be a bridge without making the average range
over a different set per period, which would destroy transitivity. Such a
period is excluded from the bridge set, still receives a level through the
remaining bridges, and the exclusion is reported in `warnings`. A window in
which no product survives from one end to the other has no bridge at all and
is refused, with the time product dummy named as the alternative.

**TPD and WTPD** assume each product's quality is constant over the window —
that is exactly what one dummy per product says — which is a real assumption
over twenty-five months and the reason the window is not simply made longer.
The unweighted form gives a clearance line with one unit sold the same say as
the product beside it with ten thousand; the weighted form does not, and
normalises each period to total weight one so no period outvotes another by
being a bigger trading month.

**TDH** makes the strongest assumption of the six: the characteristics must
explain quality and their valuation must hold still over the window. In
exchange it prices a product it has never seen, so a new product contributes
from its first period. The fit's VIF, condition number and out-of-sample error
are the evidence for the assumption and are returned alongside the index.

**Geary-Khamis** assumes one fixed reference price per product across the
window — that the relative valuation of products does not respond to the
relative prices they trade at. On data where a product is bought *because* it
is on promotion, that is the thing being assumed away.

## Known biases

Matched-model methods (GEKS) are biased by what they discard: churn. Dummy
methods (TPD, WTPD) are biased by the constant-quality assumption inside the
product dummy, which absorbs genuine quality change as a level difference.
TDH's bias follows its specification, so an omitted characteristic becomes
price change. Geary-Khamis is additive and therefore consistent in aggregation
— the reason an office that must publish contributions alongside the headline
tends to end up there — at the cost of a stronger substitution assumption than
any superlative method makes.

None of them is right. On collected price quotes they agree to a rounding
error; on scanner data with real churn they can differ by more than the
inflation being measured. `method_comparison` computes the same data under
every method, window and rule and reports the spread in index points and in
percentage points of the annualised rate, and the Multilateral page shows it,
because a judgement the reader cannot see is a judgement nobody made.

## What the engine does not do

The window is applied to the collection as a whole, not per category: a
per-category window length, and a per-category choice of method, are not
implemented. Neither is a multilateral aggregate rolled up through the
classification tree — the multilateral series is computed over the products in
the panel directly, and the weighted aggregate in `engine/aggregation.py`
operates on bilateral category indices. There is no seasonal variant (a
year-over-year window, or the seasonal GEKS forms), and no standard error on a
multilateral level.
