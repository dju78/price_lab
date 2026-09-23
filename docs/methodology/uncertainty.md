# Sampling uncertainty (`engine/uncertainty.py`)

## The design first

Price quotes are not a simple random sample. Outlets are sampled, often
within strata, and every quote from one outlet shares that outlet's pricing.
Resampling individual quotes treats them as independent and, on clustered
data, reports an interval narrower than the truth — worse than reporting
none, because it is believed.

So an interval is computed only for a **declared** design: the column naming
the primary sampling unit (the cluster, usually the outlet) and, optionally,
the column naming the strata. The declaration is recorded with the interval
and in the audit log. A collection uploaded with no design information gets
no interval: `DesignUnknown`, and in place of the interval every headline
says "Sampling uncertainty has not been quantified for this figure", with the
reason. Simple random sampling of quotes is never assumed.

## The bootstrap

Within each stratum, clusters are drawn with replacement, whole, with every
quote they hold. Following Rao and Wu (1988), n_h − 1 clusters are drawn
from a stratum of n_h, which makes the bootstrap variance of a mean match the
with-replacement design variance without reweighting. No finite population
correction is applied, which errs wide where a large share of a stratum is
sampled. A stratum holding a single cluster has no variation to resample and
is refused, with an instruction to collapse it with a similar stratum and
declare that — rather than its variance being set to zero in silence.

The statistic is the direct matched-model movement of the headline between
two periods: in each category the geometric mean of the relatives of items
priced in both, the categories combined as the run combines them. The
interval is the percentile interval of 999 replicates.

## Does it cover?

`tests/test_uncertainty.py` draws 400 stratified cluster samples (30 of 200
outlets per stratum, outlet effects shared by their items) from a population
whose true movement is known, and bootstraps each. The 95% interval contained
the truth in **95.5%** of them (tolerance 92–98%, about ±2.75 standard errors
of a coverage estimate from 400 samples). The naive interval, resampling
quotes, contained it in **65.5%**, and was on average **2.06 times narrower**.

## What it is not

An interval measures how much the movement would differ had a different
sample been drawn by the same design. It says nothing about choices of method,
which the sensitivity range measures ([sensitivity.md](sensitivity.md)). The
two are never combined into one band, and `reporting/charts.check_axes`
refuses an axis that draws both.

## Citation

Rao and Wu, "Resampling inference with complex survey data", *Journal of the
American Statistical Association* (1988). CPI Manual 2020, the chapter on
errors and bias, on sampling variance in price indices.

## What the module does not do

No finite population correction, no variance for chained indices across many
links, no linearisation (Taylor) variance to compare with the bootstrap, and
no design weights beyond the strata and clusters declared.
