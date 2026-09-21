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
the figure from it. The methodology notes under `docs/methodology/` state
each formula, its citation, assumptions and known biases.

---

## 5. Auditor or reviewer (role: viewer)

You reproduce a published number from source data and confirm nothing was
changed silently.

1. Start from the export you were given. Read its stamp
   (`pricelab.reporting.readback` reads it from a CSV, Markdown, Word, deck,
   Excel, PDF or SDMX file) for the run identifier and data vintage.
2. On **Reports → Load an approved run**, load that run identifier. The
   registry reproduces it; the headline shown is the headline the registry
   recorded at registration, which is the number the bulletin printed.
3. The data vintage is the raw layer's file name in the Parquet store; its
   receipt names the file, the person and the time of upload; its
   transformation log replays the cleaned layer.
4. The audit log is append-only and hash-chained; the evidence pack's audit
   extract lists every event for the run, and `verify_chain` confirms no
   entry was altered. Every quality-adjustment approval, validation
   override, configuration change, calculation and export is in it, with
   who and when.
5. Every disclosure-control decision is visible: a suppressed cell says so
   and names the rule.

`tests/test_end_to_end.py` walks this exact chain, from a PDF bulletin back
to individual source quotes, as an automated test.
