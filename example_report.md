# Prices rose 36% over 11 years, 2.8% a year

*Supermarket collection*

6,600 observations · 87 items · 10 categories · Jan 2015 to Dec 2025 · 230 data faults repaired

Produced 07 September 2026 by PriceLab.

## Summary

1. **230 observations (3.5%) are unit errors, and they are recoverable** (230 of 6,600 records (3.5%); 0 residual outliers after repair)
2. **Prices rose 36% overall, 2.8% a year** (index 135.6 at Dec 2025, 2.83% annualised)
3. **Inflation peaked at 3.5% in October 2020 and has since eased to 2.7%** (peak 3.5% (Oct 2020), latest 2.7%, trough 1.8% (Jul 2016))
4. **Ignoring item replacement would misstate Sun cream by 15 percentage points** (Sun cream -15.1pp · Coffee -11.2pp · Chicken -9.9pp · Pasta +9.4pp)
5. **Category rates diverge by 5.8 percentage points a year, from Ice cream to DVDs** (Ice cream 4.8% · Biscuits 4.4% · Chicken 4.0% … DVDs -1.0%)

## Price movement

### Prices rose 36% overall, 2.8% a year

The all-items index reaches 135.6 at December 2025 against a base of 100 at January 2015. With no expenditure weights supplied this aggregate is an equally weighted geometric mean of the category indices, so it is indicative of direction and broad magnitude rather than an authoritative headline rate.

*Evidence: index 135.6 at Dec 2025, 2.83% annualised*

**Action.** Supply expenditure weights to turn this into a publishable headline rate.

### Inflation peaked at 3.5% in October 2020 and has since eased to 2.7%

The twelve period rate traces a clear cycle rather than a steady trend. The peak sits in October 2020 and the rate at the end of the series is 2.7%. A single average over the whole period would hide this shape entirely.

*Evidence: peak 3.5% (Oct 2020), latest 2.7%, trough 1.8% (Jul 2016)*

### Category rates diverge by 5.8 percentage points a year, from Ice cream to DVDs

Ice cream runs at 4.8% a year while DVDs runs at -1.0%. Divergence of this size means the aggregate conceals more than it reveals, and any weighting decision will move the headline materially.

*Evidence: Ice cream 4.8% · Biscuits 4.4% · Chicken 4.0% … DVDs -1.0%*

### DVDs is in sustained deflation

Prices fall at 1.0% a year over the full period, against a rising aggregate. A category moving persistently against the general trend usually reflects something structural in the product rather than the price environment, though this data cannot establish what.

*Evidence: DVDs -1.0% a year*

## Data quality

### 230 observations (3.5%) are unit errors, and they are recoverable

117 values sit around one hundred times the level of their own item and 113 sit around one hundredth of it. The multipliers cluster tightly rather than forming a continuous tail, which is the signature of a unit of measurement fault rather than genuine price volatility. Because the mechanism is known, the true value can be restored by rescaling, so the observations were repaired rather than deleted. Deleting them would have broken the item continuity that a matched index depends on.

*Evidence: 230 of 6,600 records (3.5%); 0 residual outliers after repair*

**Action.** Review the flagged list before publication. If the collection system can be changed, a field-level unit validation at entry would remove this class of error at source.

### Strawberries is seasonally unavailable, not seasonally priced

308 observations are absent, recurring in the same calendar months (January, February, March, April, October, November, December) across the collection period and affecting every item at once. This is a product that leaves the shelf, not a price that falls. That distinction matters because a category whose seasonality lives in availability needs a seasonal index treatment, whereas one whose seasonality lives in price does not.

*Evidence: 308 gaps across 77 periods, January, February, March, April, October, November, December*

**Action.** Apply an explicit seasonal method to Strawberries and state it. Holding the level across the out-of-season gap is defensible, but it must be a stated choice rather than a side effect.

### Pasta has a collection failure, March 2020 to May 2020

Every item in the category is unpriced for 3 consecutive periods, then resumes. The pattern does not recur, so this is non-response rather than seasonality. Left untreated the index simply skips the gap, which understates or overstates the movement across it depending on what happened to prices meanwhile.

*Evidence: 18 gaps, Mar 2020 to May 2020, all items affected*

**Action.** Impute Pasta across the gap, class mean by default, and report the sensitivity of the headline rate to that choice.

### 363 missing values are coded as a sentinel, not left blank

Treating a sentinel value as a price of nil would drag every affected average towards zero and, on a geometric index, make the comparison undefined. They have been recoded to unavailable before any calculation.

*Evidence: 363 of 6,600 records (5.5%)*

**Action.** Confirm the sentinel list matches the collection system's conventions.

### Biscuits has 4 isolated price gaps

Gaps are scattered across items and periods rather than systemic, consistent with ordinary non-response.

*Evidence: 4 gaps across 4 periods*

**Action.** Class mean imputation is usually adequate for scattered gaps.

### Bread has 4 isolated price gaps

Gaps are scattered across items and periods rather than systemic, consistent with ordinary non-response.

*Evidence: 4 gaps across 4 periods*

**Action.** Class mean imputation is usually adequate for scattered gaps.

### Chicken has 4 isolated price gaps

Gaps are scattered across items and periods rather than systemic, consistent with ordinary non-response.

*Evidence: 4 gaps across 4 periods*

**Action.** Class mean imputation is usually adequate for scattered gaps.

### Coffee has 6 isolated price gaps

Gaps are scattered across items and periods rather than systemic, consistent with ordinary non-response.

*Evidence: 6 gaps across 6 periods*

**Action.** Class mean imputation is usually adequate for scattered gaps.

### DVDs has 4 isolated price gaps

Gaps are scattered across items and periods rather than systemic, consistent with ordinary non-response.

*Evidence: 4 gaps across 4 periods*

**Action.** Class mean imputation is usually adequate for scattered gaps.

### Ice cream has 2 isolated price gaps

Gaps are scattered across items and periods rather than systemic, consistent with ordinary non-response.

*Evidence: 2 gaps across 2 periods*

**Action.** Class mean imputation is usually adequate for scattered gaps.

### Laundry detergent has 5 isolated price gaps

Gaps are scattered across items and periods rather than systemic, consistent with ordinary non-response.

*Evidence: 5 gaps across 5 periods*

**Action.** Class mean imputation is usually adequate for scattered gaps.

### Sun cream has 5 isolated price gaps

Gaps are scattered across items and periods rather than systemic, consistent with ordinary non-response.

*Evidence: 5 gaps across 5 periods*

**Action.** Class mean imputation is usually adequate for scattered gaps.

## Sample structure

### In Chicken, replacement items arrive 25% above the category level of the month they enter

The composition of the sample is shifting in price level, not only the prices within it. Each entrant is compared against its own category in the month it arrives, so this is not simply the effect of joining a rising series. A matched index removes this from the measured movement; an unmatched average would report it as inflation. The data shows the shift, it does not show why it is happening.

*Evidence: Chicken +25% · Laundry detergent +2% · Strawberries +1% · Biscuits +0%*

**Action.** Check whether replacements are like-for-like in specification. If they are not, the index needs a quality adjustment this tool does not apply.

### The sample rotates: 87 items across the period, 5.0 priced in a typical category period

Items enter and leave throughout, so the collection is a rotating panel rather than a fixed basket. This is the structural fact that forces a matched-model index: comparing whatever happens to be in the sample from one period to the next would count replacement as price change.

*Evidence: 87 distinct items, median lifespan 87 periods*

**Action.** Keep the matched-model comparison. Where replacement is frequent, consider whether the specification changed alongside the item.

### 12 category periods rest on fewer than three matched items

An index built on two items is a far weaker statistic than one built on six, and the difference is invisible in the published series. Out-of-season periods are excluded from this count, so these are genuine coverage gaps. Coverage should be reported alongside the index rather than buried.

*Evidence: worst affected: Strawberries (11 periods)*

**Action.** Publish the matched count alongside the index, and consider suppressing periods below a stated minimum.

## Seasonality

### Ice cream swings 88% within the year, peaking in July

5 categories show a within-year cycle of at least 10%, including Sun cream, Biscuits, DVDs. The amplitude is measured on the index rather than on raw prices, because raw prices confound the seasonal cycle with changes in which items happen to be in the sample. A cycle of this size means any month-on-month comparison is close to meaningless without adjustment.

*Evidence: Ice cream 88% (peak Jul) · Sun cream 81% (peak Jul) · Biscuits 41% (peak Dec) · DVDs 30% (peak Dec)*

**Action.** Compare twelve period rates rather than consecutive periods, or apply seasonal adjustment before publishing a short-term movement.

### Strawberries is seasonal in availability but flat in price

The category disappears for part of every year, yet while on sale its price barely moves within the season. These are two different methodological problems wearing the same word, and applying a price-cycle adjustment here would correct for a cycle that does not exist.

*Evidence: in-season amplitude 2.4%, absent 308 observations*

**Action.** Treat as a seasonal item by availability. Consider whether the category should contribute to the aggregate out of season at all.

## Method and sensitivity

### Ignoring item replacement would misstate Sun cream by 15 percentage points

Comparing a simple average of prices from one period to the next overstates Sun cream, Coffee, Chicken and understates Pasta, Biscuits, Laundry detergent against the matched comparison. Where replacements enter dearer than the items they replace, the naive measure counts the change in the sample as inflation; where they enter cheaper it does the reverse. Over a long collection this choice moves the answer further than any outlier rule.

*Evidence: Sun cream -15.1pp · Coffee -11.2pp · Chicken -9.9pp · Pasta +9.4pp*

**Action.** Keep the matched comparison. If a naive average is required for continuity with an older series, publish both.

### A fixed base index is impossible for 5 categories

In Coffee, Ice cream, Pasta, Strawberries, no item survives from the base period to the last, so there is no direct comparison to make. Chaining is not a stylistic preference here, it is the only way to link the two ends of the series. That also means the series carries whatever chain drift the rotation introduces.

*Evidence: 5 of 11 categories have no surviving item*

**Action.** Accept chaining and monitor drift where the category is strongly seasonal.

### The choice of elementary formula moves the result by up to 3.2 index points

The same cleaned data aggregated with Jevons, Dutot and Carli gives a spread of 3.2 points at the final period. Carli sits highest, which is its known upward bias: it fails the time reversal test, so chaining it forward and back does not return to the starting point. This run uses Jevons, which satisfies time reversal and is invariant to the units in which each item is quantified.

*Evidence: spread 3.2 points at Dec 2025*

**Action.** Retain a geometric formula unless the items in an aggregate are genuinely homogeneous and comparably quantified.

## Index levels

|                   |   Index at Dec 2025 |
|:------------------|--------------------:|
| Biscuits          |              159.88 |
| Bread             |              127.93 |
| Chicken           |              154.16 |
| Coffee            |              114.13 |
| DVDs              |               89.54 |
| Ice cream         |              167.33 |
| Laundry detergent |              135.73 |
| Pasta             |              151.62 |
| Strawberries      |              133.91 |
| Sun cream         |              141.17 |
| All items         |              135.57 |

## Method note

**Source.** 6,600 observations covering 87 items across 10 categories, January 2015 to December 2025.

**Missing values.** 363 observations carried a missing code and were recoded to unavailable before any calculation, rather than being treated as a price of nil. Gaps were classified by mechanism before treatment: Biscuits was classified as sporadic (4 observations); Bread was classified as sporadic (4 observations); Chicken was classified as sporadic (4 observations); Coffee was classified as sporadic (6 observations); DVDs was classified as sporadic (4 observations); Ice cream was classified as sporadic (2 observations); Laundry detergent was classified as sporadic (5 observations); Pasta was classified as collection (18 observations); Strawberries was classified as seasonal (308 observations); Sun cream was classified as sporadic (5 observations).

**Outlier detection.** Each observation was compared against a 13 period centred rolling median of its own item's series. A centred local median was used in preference to a whole period median because prices trend across a long collection, and a fixed reference would flag genuine later prices as faults. Observations whose deviation from that local level fell between 1.5 and 2.5 in log10 units were treated as unit of measurement errors. A band was used rather than a simple threshold because a unit fault has a known multiplier, whereas genuine volatility does not cluster at a fixed ratio.

**Treatment.** 117 observations were rescaled down and 113 rescaled up. They were repaired rather than deleted, because deletion would break the item continuity that a matched comparison depends on. After treatment, 0 observations remained beyond 0.5 in log10 units of their local level.

**Imputation.** The default imputation method was no imputation. Category overrides: Biscuits treated by class mean; Bread treated by class mean; Chicken treated by class mean; Coffee treated by class mean; DVDs treated by class mean; Ice cream treated by class mean; Laundry detergent treated by class mean; Pasta treated by class mean; Strawberries treated by seasonal hold; Sun cream treated by class mean.

**Aggregation.** A Jevons elementary index was used, matched model, chained period on period, set to 100 at January 2015. Only items priced in both of the two periods being compared enter that comparison, so the entry or exit of an item does not register as price change. Periods with fewer than 2 matched items hold the previous level. The all-items aggregate is an equally weighted geometric mean of the category indices. No expenditure weights were supplied, so it is indicative rather than authoritative.

**Limitations.** No quality adjustment is applied between a departing item and its replacement, so any change in specification is implicitly treated as price change. Seasonal treatment is limited to holding the level across an out-of-season gap. The tool reports what the collection shows; it does not establish why any movement occurred.