# PriceLab user guide

Organised by who you are. Each section is a path through the application:
which pages you can reach, what you do on them, and what you take away.
Roles are enforced from inside each page, not by hiding menu entries; if a
page below is not in your sidebar, ask an administrator for the role that
has it.

| Role | Pages |
|---|---|
| `administrator` | everything, plus approving registered runs |
| `compiler` | Ingest, Quality, Imputation, Quality adjustment, Index build, Findings, Diagnostics, Reports |
| `analyst` | Findings, Diagnostics, Reports |
| `viewer` | Reports |

Sign in with the username and password an administrator created for you
(`scripts/create_user.py`; there is no self-registration). A session idle
for longer than the configured timeout (default 60 minutes) ends and you
sign in again.

---

## 1. Official statistician (role: compiler)

You compile an index from raw quotes, treat gaps and replacements, and
release a number that can be reproduced.

**Ingest.** Upload a CSV or Excel workbook (up to the configured size; at
most the configured number of uploads per minute). Confirm the column
mapping — the suggestion is confidence-scored and, once confirmed, is
remembered for that exact file. Read the validation report: a critical
finding blocks compiling until you accept, exclude, correct or justify it,
and your decision is recorded with your name. Review the automatic
diagnosis (missing codes, fault band, imputation method per category from
the gap mechanism), change what you disagree with, choose the formula and
chaining, and compile. If the upload carries a quantity column (or
expenditure, from which quantity is derived), the quantity-weighted
formulae -- Paasche, Fisher, Törnqvist, Walsh, Marshall-Edgeworth, the
geometric forms and the unit value -- become available and Laspeyres uses
the quantity basket; on a price-only upload they stay listed with the
reason they cannot be compiled. Every upload is written once to the
immutable raw Parquet layer with a receipt naming you, the file and the
time; every compile writes the cleaned layer and its transformation log.

**Quality** and **Imputation.** Every recoded sentinel, every repaired fault,
every imputed value, with the flag naming what was done and the response
rate per category and period ([methodology/data_quality.md](methodology/data_quality.md),
[methodology/imputation.md](methodology/imputation.md)).

**Quality adjustment.** The page lists replacement candidates (an item that
left, an item that arrived in the same category soon after). Value a
replacement by any of the Chapter 6 methods — overlap, direct comparison,
quantity, option cost, targeted/overall/class mean, or hedonic once you have
uploaded a characteristics file and fitted a model — read the adjustment in
price terms and index points before approving it, and write a justification
(required). Approval records the valuation against this data, audits it,
and recompiles with the ledger in the configuration. The impact section
then says how much of the headline is the adjustments, in points and in
percentage points of annual inflation ([methodology/quality_adjustment.md](methodology/quality_adjustment.md)).

**Index build**, **Findings**, **Diagnostics.** The series, the ranked
findings in plain English, and the consequences of your choices
(sensitivity to formula, chain drift, matched versus unmatched).

**Outliers.** Four screens over period-on-period price relatives, and a
review queue. Nothing here deletes anything: a flagged quote is a question,
and you answer it with accept, reject or annotate and a written reason that
goes to the audit log with your name on it. Only a reject excludes the
quote, and even then the row stays in the collection, marked, so the
exclusion can be reviewed. The count of exclusions is reported as a share of
the quotes they would have fed, in the same units the imputation rate is
([methodology/outliers.md](methodology/outliers.md)).

**Seasonality.** Two different things under one word. Strictly seasonal
items — off the shelf for part of every year — get both treatments, class
confinement and weight update, with the gap between them reported, because
that gap is a judgement about what the basket means while part of it does
not exist. Separately, seasonal adjustment: read the engine name before you
read the line. STL is the working method; X-13ARIMA-SEATS runs only where
an administrator has enabled it and it is installed, and is then labelled
an unvalidated path. The page says which applies, and every output says
which one produced the series. The
unadjusted series is drawn on the same axes and written into the same file,
and every output says the adjustment was direct: seasonally adjusted
components need not add up to an adjusted total, so do not sum them and
expect it to match ([methodology/seasonal.md](methodology/seasonal.md)).

**Decomposition.** What is driving the headline, and how broad it is. Work
from your own compiled run (it needs expenditure weights; without them the
page says why nothing additive can be computed) or fetch the published HICP
from Eurostat with its item weights. The page gives every standard rate of
change side by side; contributions at every level of the classification
tree, with the reconciliation gap to the headline shown (it should read of
order 1e-14 percentage points); core measures — exclusion, trimmed mean,
weighted median, variance weighted, sticky price — each with its parameters
and, where the data cannot support it, the reason; the base-effect split of
the year-on-year rate into carry-over and this year's impulse; and the share
of the basket rising, the spread and the skew
([methodology/decomposition.md](methodology/decomposition.md)).

**Deflation.** Upload a nominal series (earnings, income, sales) as a
two-column CSV and deflate it by a series of the compiled index, an official
series fetched on Sources, or an uploaded deflator. Pick the reference
period — a month, or a whole year — and the result names the deflator and
the reference period on screen, on the chart and on the download's first
line. If the two series have different frequencies you get an error, not a
quietly resampled answer: choose how to convert the nominal series (average,
sum for a flow, last value for a stock) and the conversion is recorded with
the result. The same page converts values at purchasing power parities, and
asks before holding an annual PPP constant through the months
([methodology/deflation.md](methodology/deflation.md)).

The Decomposition page, on the published HICP, also shows each division's
contribution to the **annual** rate across the December re-weighting, by the
published (Ribe) treatment: this year's weights since December, last year's
before. These are the numbers Eurostat publishes, and they add up to the
annual rate exactly. The bulletin, the Word report and the deck now carry a
"Contributions to the change" table for your run too, with the level of the
tree and the residual shown.

**Spatial comparison.** Upload one price per region and product. Choose
country product dummy (works with gaps, gives standard errors) or
Geary-Khamis (needs quantities; weights large regions more). Read the
matched-products table before the parities: a region sharing fewer products
than the threshold is shown but not published, and is not converted
([methodology/spatial.md](methodology/spatial.md)).

**Trade prices.** Upload transactions with a flow (export or import), a
product, a value and a quantity. The page gives the price index and, beside
it, the unit value index with the gap between them. A unit value index is
only a price index when the products pooled are homogeneous and their mix
is stable; the page says so every time it shows one
([methodology/trade.md](methodology/trade.md)).

**Construction.** Two different indices: the input cost index (what the
builder's inputs cost) and the output price index (what the client pays for
finished work of fixed specification). The page states which is which
before either number, and the gap between them is margins and
productivity, not an error ([methodology/construction.md](methodology/construction.md)).

**Contract escalation.** Pick the index — a series of your run, an official
series, or an uploaded one with its vintage stated — set the clause (lag,
averaging, indexed share, dead band, trigger, cap, collar) and the payment
periods. Read the summary above the schedule: it restates the clause in
words and names every month in which a cap or collar bound, with the payment
it would otherwise have been. Download it beside the CSV for the contract
file ([methodology/escalation.md](methodology/escalation.md)).

**Property prices.** Upload transactions (property_id, period, price,
stratum, floor_area, and appraisal if you have one), or use the
demonstration market. "Compile all methods" runs the five families on the
same sales: each number comes with what it measures and what it rests on,
and "Why they differ" explains the gaps with numbers — how much the quality
of what sold changed, how much of that each method carries, how many repeat
pairs there are. Below: sales per stratum, the share of sales each method
uses, the per-stratum table with thin strata suppressed, and the repeat
sales revision profile, which also appears on the Revisions page. The last
section rebuilds a country's published house price index from its published
parts ([methodology/asset.md](methodology/asset.md)).

**Rents and owner-occupied housing.** A rental price index from matched
rents, and the four owner-occupied housing approaches — each in its own
section under the question it answers. Decide which question you are asking
before looking at any of them; they are not four estimates of one number
([methodology/housing.md](methodology/housing.md)).

**Uncertainty.** Two different questions, in two sections. *Sampling
uncertainty*: name the column that identifies the outlet (the primary
sampling unit) and, if the sample was stratified, the strata; the page then
bootstraps the headline's movement by resampling whole outlets within
strata, and shows beside it how much narrower an interval that ignored the
design would have been. If you do not know how the sample was drawn, leave
the design unset: no interval is published, and every headline says so.
*Methodological sensitivity*: the headline recompiled under each defensible
alternative choice, with the highest and lowest settings named. It is not a
confidence interval, is never drawn with one, and must not be added to one.
It is also a lower bound: one choice is varied at a time, so what choices do
together is not in it. Beside it is the imputed share of the aggregate, in
the published run and under each imputation setting. Read the imputation
rows against that share: a large swing from filling a small share is a
decision about a few items, not a general property of the index. Every
headline in the analyst view carries its interval or a statement that none
has been quantified.

**Forecasts.** Choose a method: ARIMA, SARIMAX, exponential smoothing, or a
pass-through or Phillips-curve regression on a driver series you upload with
its source. Then choose a horizon and the number of backtest origins. Read
the verdict first. It says whether the model beats the naive benchmark (the
random walk, or the seasonal naive for a seasonal series) on the same
backtest, and it appears above the number, not in a panel. A model that does
not beat the benchmark is a finding. Then read the backtest line. Where the
error the model actually made is wider than the interval it claims, the page
says the model is understating its own uncertainty. Regression coefficients
are correlational, and the page says so each time it shows one. Downloads
(CSV, Excel, Markdown) carry the interval, the backtest, the benchmark
comparison and every assumption, or are refused. *Register this forecast*
records it against the run, so the registry can rebuild it and check its
backtest to the last digit
([methodology/forecasting.md](methodology/forecasting.md)).

**Scenarios.** Not a forecast, and never labelled as one. Tick a shock
(energy prices, the exchange rate, wages, administered prices) and give:
- its size, the month it starts and how long it takes to pass through;
- a coefficient with its source, taken in one of three ways:
  - stated with a source you name;
  - the weight of chosen categories in the aggregate (the direct effect
    only);
  - the long-run coefficient of a pass-through regression from the Forecasts
    page.

The path is drawn in a fan built from the baseline rule's past errors. The
assumption list sits directly under the chart and is part of the scenario. A
scenario with any assumption unstated can be looked at but cannot be
exported ([methodology/scenarios.md](methodology/scenarios.md)).

**Property prices from HM Land Registry.** Upload a price paid file exactly
as published (headerless; an extract for a district keeps within the upload
limit). The page shows what the file held and what was excluded, rule by
rule, then runs the methods with the settings the data allows.

The Excel evidence pack now has a **Contributions** sheet: the same table as
the bulletin, with the tree level and the residual row, where the arithmetic
can be checked in place.

**Multilateral.** Only if your collection carries quantities or
expenditure, and only worth it if it is transaction or scanner data:
products churning, prices bouncing between shelf and promotion, quantities
following. A chained bilateral index on data like that accumulates the
bounce as inflation. Pick a method (GEKS-Fisher, GEKS-Törnqvist, the time
product dummy weighted or not, the time dummy hedonic if you have uploaded
characteristics, or Geary-Khamis), a window and an extension rule, and the
page computes the series, the gap to the chained bilateral, and the spread
across every method and rule at once. Read the spread before you read the
number: on churning data the choice of method can move the answer by more
than the inflation you are measuring
([methodology/multilateral.md](methodology/multilateral.md)).

**Revisions.** Once a run has been corrected, this shows the revision
triangle: what each vintage said about each reference period, the mean and
mean absolute revision, and whether the revisions have a direction. Every
earlier vintage is still registered and still reproduces; a correction adds
a vintage and never edits one
([methodology/revision.md](methodology/revision.md)).

**Reports.** Register the run: the registry records the input's content
hash, the complete configuration, the code version, the environment and
the headline figure. An administrator approves it, after which it is
immutable; a later correction is a new vintage with a stated reason. Every
download — deck, Word report, Excel evidence pack, CSV, SDMX-ML, and once
registered the PDF bulletin — carries the same provenance stamp.

---

## 2. Macroeconomist / central bank analyst (role: analyst)

You interpret a compiled index rather than compile one.

**Reports → Load an approved run.** Pick a registered, approved run; the
registry reproduces it exactly from its stored input and configuration
(and warns if that configuration predated the reference-period split).

**Findings.** Ranked findings with evidence: peak twelve-month rates, trend
breaks, seasonal patterns, structural churn. **Diagnostics.** How sensitive
the headline is to the elementary formula and to chaining, which is the
range within which method rather than prices determines the answer.

**Index build.** Year-on-year rates, the three reference periods the series
was compiled under, and the "= 100" period every label refers to.

**Revisions.** How much the figure you are about to use has moved since it
was first published, and whether it has moved in one direction. A first
estimate revised up two years in three is not early, it is biased.

**Multilateral.** Where the run carries quantities, the comparison table is
the honest statement of how much of the headline is method rather than
price: six methods, two window lengths and six extension rules on one
collection, with the spread in index points and in percentage points of
the annualised rate.

**Forecasts and Scenarios.** A forecast here is always read with its benchmark
verdict and its backtest. A scenario is always read with its assumptions. A
pass-through coefficient measures how two series have moved together, not
what one does to the other. See the compiler's notes above.

Take away: the Markdown or Word report with its method note, and the CSV
publication table with disclosure control applied.

---

## 3. Corporate or procurement analyst (role: compiler or analyst)

You track supplier prices or build an input-cost index from your own data.

Upload your price list, tender or invoice extract on **Ingest** with one
row per item per period; map your columns to period, item, category and
price (and weight, quantity or expenditure, if you have them: quantities
unlock Fisher and the other superlative formulae). Compile. Use a custom
formula only if you need one, knowing every export will be marked
non-standard ([methodology/custom.md](methodology/custom.md)). Use the
**Quality adjustment** page when a supplier substitutes a product: the
option-cost and quantity methods are built for pack-size and specification
changes.

Take away: the Excel evidence pack (source data, weights, elementary and
upper-level aggregates, ledger, final index, methodology log, audit
extract) and the CSV, both stamped, for contract escalation or budget
deflation work downstream.

---

## 4. Policy researcher (role: analyst)

You need reproducible outputs and an evidence pack you can cite.

Load an approved run on **Reports**. Every export carries the provenance
stamp: run identifier, data vintage (the content hash of the input, which
is also its raw-layer file name), code commit, complete parameters,
suppression rules, whether a non-standard formula was used, and the time.
Cite the run identifier; anyone with access to the registry can reproduce
the figure from it. The code commit is the full hash, marked "with
uncommitted changes" when the run was made from a working tree that differed
from it -- register publication runs from a clean checkout, so they trace to
exactly one commit. The methodology notes under `docs/methodology/` state
each formula, its citation, assumptions and known biases.

---

## 5. Auditor or reviewer (role: viewer)

You reproduce a published number from source data and confirm nothing was
changed silently.

1. Start from the export you were given. Read its stamp
   (`pricelab.reporting.readback` reads it from a CSV, Markdown, Word, deck,
   Excel, PDF or SDMX file) for the run identifier and data vintage.
2. On **Reports → Load an approved run**, load that run identifier. The
   registry reproduces it from its stored input and configuration with the
   code running now; the headline shown is the headline the registry
   recorded at registration, which is the number the bulletin printed. On
   **Audit → Verify a registered run**, the verification also says whether
   the code running now is the commit that registered the run; only then is
   the reproduction a replay of the original rather than evidence about the
   current code.
3. The data vintage is the raw layer's file name in the Parquet store; its
   receipt names the file, the person and the time of upload; its
   transformation log replays the cleaned layer.
4. The audit log is append-only and hash-chained; the evidence pack's audit
   extract lists every event for the run, and `verify_chain` confirms no
   entry was altered and none deleted from the middle. It cannot tell a log
   whose most recent entries were deleted, or an emptied log, from an
   intact one. Every quality-adjustment approval, validation
   override, configuration change, calculation and export is in it, with
   who and when.
5. Every disclosure-control decision is visible: a suppressed cell says so
   and names the rule.

`tests/test_end_to_end.py` walks this exact chain, from a PDF bulletin back
to individual source quotes, as an automated test.
