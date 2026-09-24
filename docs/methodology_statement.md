# How PriceLab compiles a price index: methodology statement

PriceLab 1.0.0. This statement is written for a statistician who will not
read the code. It says what the platform does to a price collection to
produce an index, which standard each step follows, what the defaults are,
and what has and has not been checked against published figures. Each topic
points to the methodology note that gives the formulae and the reasoning
(`docs/methodology/`, 28 notes). The known limits of every step are in
`docs/limitations_register.md`.

## 1. Standards followed

The reference throughout is the *Consumer Price Index Manual: Concepts and
Methods* (IMF, ILO, OECD, Eurostat, UNECE and World Bank, 2020), called "the
Manual" below. Its 2004 predecessor and the *Consumer Price Index Theory*
companion volume are cited where the 2020 edition defers to them. Other
standards, where a step is not in the Manual:

| Area | Standard | Note |
|---|---|---|
| Elementary and bilateral indices | Manual ch. 8; *CPI Theory* ch. 6 | [elementary](methodology/elementary.md), [bilateral](methodology/bilateral.md) |
| Chaining, rebasing, reference periods | Manual ch. 9 | [index](methodology/index.md), [splicing](methodology/splicing.md) |
| Upper-level aggregation and contributions | Manual ch. 9; OECD (2018, rev. 2022) and Eurostat *HICP Methodological Manual* ch. 8 for contributions across a chain link | [aggregation](methodology/aggregation.md), [decomposition](methodology/decomposition.md) |
| Data validation, editing, missing prices | Manual ch. 6 and 11; IMF Data Quality Assessment Framework dimensions | [data_quality](methodology/data_quality.md), [imputation](methodology/imputation.md), [outliers](methodology/outliers.md) |
| Quality change | Manual ch. 6 | [quality_adjustment](methodology/quality_adjustment.md), [hedonic](methodology/hedonic.md) |
| Seasonal products and seasonal adjustment | Manual ch. 11 and 14; Rothwell (1958); X-13ARIMA-SEATS (US Census Bureau) or STL (Cleveland et al., 1990) | [seasonal](methodology/seasonal.md) |
| Scanner and transaction data | Manual ch. 7 (GEKS, Geary-Khamis, time product dummy) and ch. 10 | [multilateral](methodology/multilateral.md) |
| Residential property prices | Eurostat *Handbook on Residential Property Prices Indices* (2013) | [asset](methodology/asset.md), [housing](methodology/housing.md) |
| Spatial comparison | Country product dummy and Geary-Khamis as in the ICP literature | [spatial](methodology/spatial.md) |
| Deflation, constant prices, PPPs and price level indices | *System of National Accounts 2008* ch. 15; Eurostat-OECD *Methodological Manual on Purchasing Power Parities* (2012) | [deflation](methodology/deflation.md) |
| Import and export price indices, unit values, terms of trade | IMF *Export and Import Price Index Manual* (2009) | [trade](methodology/trade.md) |
| Construction input cost and output price indices | OECD-Eurostat *Construction Price Indices: Sources and Methods* (1997) | [construction](methodology/construction.md) |
| Contract price escalation | The price adjustment clauses of the FIDIC conditions of contract (sub-clause 13.8, 1999 editions) and NEC Option X1 | [escalation](methodology/escalation.md) |
| Disclosure control | UN Fundamental Principles of Official Statistics, principle 6 | [disclosure_control](methodology/disclosure_control.md) |
| Revisions | Mean and mean absolute revision; t-test for bias | [revision](methodology/revision.md) |
| Sampling variance | Rao and Wu (1988), bootstrap for complex surveys | [uncertainty](methodology/uncertainty.md) |

## 2. The compilation, in order

A run takes one price collection and one configuration and applies these
stages, always in this order. The configuration is recorded whole with the
run (section 9), so every choice below is stated with every number it
produced.

1. **Ingest and map.** The upload is read (Excel, CSV, Parquet or JSON), its
   columns are mapped to the canonical schema, and a person confirms the
   mapping. The schema is date, item, item name, category and reported price,
   with optional weight, quantity, expenditure and unit.
2. **Validate.** Completeness, validity, consistency, uniqueness, timeliness,
   coverage, conformity to the classification, and plausibility. A critical
   finding blocks compiling until a person accepts, excludes, corrects or
   justifies it, and that decision is recorded.
3. **Data quality.** Configured missing-value codes (by default a price of 0)
   become missing, not zero. Order-of-100 unit faults (pence for pounds,
   grams for kilograms) are found against each item's own rolling median and
   rescaled, not deleted, and every repaired value is flagged. See
   [data_quality](methodology/data_quality.md).
4. **Quality adjustment.** Replacements approved in the ledger are linked
   onto the old item's series (section 4).
5. **Outlier decisions.** Quotes an analyst has rejected, with a reason, are
   removed from the index (section 6).
6. **Imputation.** Missing prices are filled if the run configures a method
   (section 5).
7. **Elementary indices** per category (section 3).
8. **Aggregation** to All items (section 7).
9. **Optional stages:** the seasonal treatments and adjustment, the
   multilateral series, and the revision analysis. Each is off by default and
   labelled wherever it appears.

## 3. Formulae available, and the default

**Default: the Jevons index (geometric mean of price relatives), chained
monthly, matched model.** At each period the category index moves by the
Jevons index of the items priced in both that period and the one before. If
fewer than two items match, the level is held (the minimum is configurable).
Jevons is the Manual's recommended elementary formula when there are no
weights: it satisfies the time reversal test and does not drift when
chained. See [index](methodology/index.md) and
[elementary](methodology/elementary.md).

| Level | Formulae | Note |
|---|---|---|
| Elementary, prices only | Jevons (default), Dutot, Carli, harmonic mean, CSWD; the unit value index, only with a stated homogeneity assertion | [elementary](methodology/elementary.md) |
| Elementary, with quantities or expenditure | Laspeyres, Paasche, Fisher, Törnqvist, Walsh, Marshall-Edgeworth, geometric Laspeyres and Paasche; Lowe and Young with a separate weight reference period | [bilateral](methodology/bilateral.md) |
| Analyst-defined | Any formula composed from the above in a restricted arithmetic language, which is parsed, never executed. A run using one is marked non-standard on every output | [custom](methodology/custom.md) |
| Multilateral (scanner data) | GEKS-Fisher, GEKS-Törnqvist, time product dummy (weighted and unweighted), time dummy hedonic, Geary-Khamis. Windows are extended by movement, window, half or mean splice, FBEW or FBMW; the default is a 25-month window with mean splice | [multilateral](methodology/multilateral.md) |

**Compilation.** Chained (default) or fixed-base. Three reference periods are
kept apart:
- the price reference period, which is the denominator of the relatives;
- the weight reference period, which is when the weights are from;
- the index reference period, when the published series reads 100.

Rebasing only rescales the finished series. See
[index](methodology/index.md) and [splicing](methodology/splicing.md).

Chain drift against the direct index is measured with every run's findings,
against a 1-point threshold, and reported when exceeded. Formula sensitivity (the headline under each alternative formula)
is on the Diagnostics page. See [diagnostics](methodology/diagnostics.md).

## 4. Quality change

**Default: the matched model.** An item is compared only with itself. When a
product leaves and a successor enters, the matched model does not link them,
which attributes the whole price gap between them to quality. The Manual
calls this an implicit adjustment, and every method note states it.

**Explicit adjustment** is by approval in a quality adjustment ledger:
- overlap pricing;
- direct comparison;
- explicit quantity adjustment;
- option cost;
- class-mean, targeted-mean and overall-mean imputation;
- hedonic valuation from a characteristics file (time-dummy,
  characteristics-price and imputation hedonics, with diagnostics);
- link-to-show-no-change, which is available but warns on use.

Every entry records the ratio, the method, the justification and the
approver. The ledger is part of the run's configuration, so it is covered by
the run's hash and reproduced with it. An impact report states what the
adjustments did to the headline, in index points and in percentage points of
annual inflation, per entry. See
[quality_adjustment](methodology/quality_adjustment.md) and
[hedonic](methodology/hedonic.md).

## 5. Missing prices

**Default: no imputation.** A missing price is left missing. Its item drops
out of that period's matched comparison, and a category with no matched items
holds its level.

Available methods, per category or for the whole run:
- carry forward;
- class mean;
- seasonal hold;
- targeted mean;
- overall mean.

The targeted and overall means are anchored to the item's last observed price
and follow the Manual's chapter 6 arithmetic. Response rates, and the share of
each category and of the aggregate that is imputed rather than observed, are
reported beside the index. See [imputation](methodology/imputation.md).

This choice matters. On the bundled demonstration collection, filling the
months when strawberries are out of season moves the December 2025 headline
by about 17 points. The Uncertainty page shows the imputed share beside every
imputation setting.

## 6. Seasonal items and outliers

**Strictly seasonal items** are detected by the regularity of their absence,
not by absence alone. Four treatments are available, and the gap between the
first two is reported:
- class confinement;
- weight update;
- the Rothwell index;
- counter-seasonal estimation, where every estimate is labelled a
  construction.

All of them are off by default. An out-of-season category then holds its
level (section 5).

**Seasonal adjustment** uses STL. X-13ARIMA-SEATS runs only where an
administrator has enabled it and the program is installed. Because
PriceLab's X-13 path has never been checked against a published official
adjustment, its outputs are labelled as an unvalidated path. The engine used
is named on every output, and a run that demands X-13 where it is not
enabled fails rather than substitute. The unadjusted series always
travels with the adjusted one. Adjustment is direct, and the outputs warn
that adjusted components need not add up to the adjusted total. See
[seasonal](methodology/seasonal.md).

**Outliers** are screened, when screening is enabled, by:
- Tukey fences and the quartile method on log relatives;
- Hidiroglou-Berthelot;
- a ratio band for thin cells.

A 5% deadband stops ordinary movements from being flagged. **No quote is ever
removed automatically.** A flag excludes nothing until an analyst rejects the
quote with a stated reason. The reason is enforced from the screen down to a
database constraint, and every decision and withdrawal is audited.
Exclusions are reported as a share of the quotes they would have fed. See
[outliers](methodology/outliers.md).

## 7. Aggregation structure

Categories are aggregated to All items:

- **with expenditure weights**, as a weighted arithmetic mean of the category
  indices (a Laspeyres-type, or Young, aggregate), rolled up through a
  classification tree where the categories are codes in one. The built-in
  tree is COICOP 2018, all 871 codes from the UN Statistics Division's
  structure file. An organisation can load its own tree. Parent weights are
  the sums of their children's; weight problems are reported, not absorbed.
  Contributions to the change add up exactly to the headline change, with any
  residual shown as its own row;
- **without weights**, as an equally weighted geometric mean, labelled
  indicative.

Contributions across an annual chain link use the Ribe decomposition
(OECD 2018/2022; Eurostat HICP Methodological Manual ch. 8). See
[aggregation](methodology/aggregation.md) and
[decomposition](methodology/decomposition.md).

## 8. Disclosure control

Every published table is built by one function, which applies:

- **primary suppression** of any cell built from fewer than a configured
  number of matched quotes (default 3);
- **secondary suppression** in any period with one suppressed category and a
  published aggregate: the smallest remaining category is suppressed as well,
  so the first cannot be recovered from the aggregate.

A suppressed cell is written as "suppressed", with the rule, never as a
blank. In SDMX it is written as an observation status with no value.
Protection covers one published total per group; the function refuses rather
than run an incomplete pass over overlapping totals. See
[disclosure_control](methodology/disclosure_control.md).

## 9. Provenance and audit

- **Run registry.** A run can be registered with:
  - its input data, stored and hashed independently of row order;
  - its complete configuration;
  - its headline figure;
  - a fingerprint of the Python and main library versions;
  - the code version: the full git commit of the checkout it ran from,
    suffixed `-dirty` when the working tree differed from that commit.

  A run registered from a clean tree therefore traces to exactly one
  commit. A `-dirty` run names the nearest commit but not the code, which
  cannot be recovered. Where there is no checkout (the Docker image excludes
  `.git`), the commit is whatever was passed in when the image was built.
  Without that, the code version is recorded as `unknown`, and the stamp
  says the code cannot be identified.
- **Reproduction.** `reproduce` re-runs a registered run's stored input and
  configuration with **the code running now**. It does not check out the
  recorded commit. The Audit page's verification compares the reproduced
  headline with the registered one, and separately reports whether the code
  running now is the registering commit. The check passes only for the same
  clean commit. When the code differs, a matching headline shows that the
  change did not move that number, not that nothing changed. To replay a run
  exactly, check out its recorded commit and reproduce it there; the
  environment fingerprint says which library versions to install. Neither
  step is automated.
- **Approval and correction.** An approved run is immutable. A change after
  approval is a correction: a new vintage with a required reason, linked to
  the one it supersedes, and the superseded vintage still reproduces.
  Revision triangles and bias tests are computed from these vintages. See
  [revision](methodology/revision.md).
- **Provenance stamp.** Every export (CSV, SDMX-ML, Excel, Word, Markdown,
  slide deck, PDF bulletin) carries one stamp. It holds the run id, the data
  vintage, the code version as above, the configuration, the suppression rules, the
  non-standard flag, the ledger count and the headline. The stamp can be read
  back out of any of those files, to say which run a file came from.
- **Data layers.** Each upload is kept as an immutable raw layer with a
  receipt (file, hash, who, when). The cleaned layer is replayable from the
  raw layer through a transformation log, and the same holds for
  characteristics files.
- **Audit log.** Loads, configuration changes, calculations, overrides,
  decisions, exports and external fetches are recorded in an append-only,
  hash-chained log. Altering a record, or deleting one that has records
  after it, breaks the chain, and the Audit page verifies it. The chain
  cannot detect records deleted from the *end* of the log, or the whole log
  being emptied: an empty or truncated log verifies as intact. Protecting
  against that needs the latest hash kept somewhere the database cannot
  change, which is not done. On storage that does not persist (the public
  demonstration), the log is emptied at every restart, and the demonstration
  says so on every page.
- **Every page states its uncertainty.** Every headline in the analyst view
  carries a sampling interval, when a design has been declared, or says it
  has none. A methodological sensitivity range, labelled a lower bound, is
  never combined with the interval. See
  [uncertainty](methodology/uncertainty.md) and
  [sensitivity](methodology/sensitivity.md).
- **Forecasts and scenarios.** Neither is part of a compiled index. Neither
  can leave the platform without its interval, its backtest, its comparison
  with a naive benchmark and its stated assumptions. See
  [forecasting](methodology/forecasting.md) and
  [scenarios](methodology/scenarios.md).

## 10. Verified against published sources, and not

**Reproduced from published figures:**

| What | Against | Result |
|---|---|---|
| Elementary index arithmetic | Manual ch. 8, Tables 8.1–8.3 | Exact at the Manual's published precision |
| Quality adjustment arithmetic | Manual ch. 6: equation 6.4, the targeted-mean chain, Tables 6.4a and 6.5, the option-cost example | Exact |
| Upper-level aggregation | Eurostat euro-area HICP: all-items index re-aggregated from its 42 published groups and weights | Within 0.004 index points in every month of 2025 |
| Contributions across a chain link | Eurostat's published HICP contributions (`prc_hicp_ctrb`), every euro-area division, 2025 | Largest gap 0.0056 percentage points (the publication's rounding) |
| Chain-linked aggregation of house price indices | Eurostat HPI totals rebuilt from new- and existing-dwelling indices and weights | Within 0.061 points (DE), under 0.02 (IE, NL, FR, ES, DK, PL, EU), 2020–2025 |
| Spatial parities | Eurostat PPPs for actual individual consumption, 2023 | Median gap 5.5%, explained by coverage (the categories with published expenditure cover about half of consumption) |
| SDMX-ML output | The SDMX 2.1 XML schemas | Validates |

**Checked only against constructed data, known processes or hand
calculation, with no published figure reproduced:**
- the superlative formulae (hand-calculated two-good examples);
- the multilateral methods;
- the seasonal treatments and STL adjustment;
- the outlier screens;
- the hedonic estimator (it recovers a known effect within 2%);
- the property methods (run on 2024 HM Land Registry transactions, but not
  compared with a published house price index);
- trade, construction and escalation;
- the bootstrap interval (95.5% coverage on a simulated population);
- the forecasting models.

**Never run against the real program:** X-13ARIMA-SEATS. It is off unless an
administrator enables it, and labelled unvalidated when on (limitation
A13).

**Tests.** The suite covers the index axioms (identity, proportionality,
commensurability, time and factor reversal) as properties over generated
data, not fixed numbers. It includes a hard-gate test: the bundled
collection must reproduce its committed baseline series after every change,
to a relative 1e-12. That is exact up to the last-digit rounding in which
Windows and Linux maths libraries differ, and far tighter than any change of
method.
