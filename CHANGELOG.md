# Changelog

All notable changes to PriceLab. Phases refer to the platform build plan in
`docs/backlog.md`; each phase is one commit and passed the same hard gate
(every existing test green, the bundled fixture's index series identical to
its committed baseline, ruff and mypy strict at zero).

## 1.0.1 — 2026-09-24

### Fixed
- **A clean install could not run the tests.** `pypdf` was imported by
  `reporting/readback.py`, which reads the provenance stamp back out of a
  PDF bulletin, and by three tests, but it was declared nowhere. It was
  present only in the development environment. 1.0.0's first CI run (Python
  3.12, Linux, both backends) therefore failed at test collection, after
  installation, lint and type-checking had passed.
  - `pypdf` is now a dependency.
  - `pillow`, which the deck imports directly, is declared too, instead of
    relying on python-pptx to bring it.
  - `tests/test_release.py` fails on any third-party import, in the package,
    the pages, the app or the tests, that `pyproject.toml` does not declare.

The reproduced-image check in 1.0.0 could not catch this: it ran the
image's layout in the development environment, not a clean install of the
declared dependencies. `docs/release_verification.md` now says so.

## 1.0.0 — 2026-09-24

The first release of PriceLab as a governed price statistics platform. It
covers the whole build plan: Phases 0 to 10, the quantity and expenditure
ingestion work, the Phase 10.5 wiring audit, and this release's close-out.
The phase entries below are its detailed history.

### What the platform does
- **Compiles a price index from a price collection, and shows every step.**
  - Uploads are mapped by a person, validated on eight quality dimensions,
    and blocked on a critical finding until someone decides it.
  - Unit faults are repaired with a flag; missing codes are recoded.
  - Elementary indices use Jevons by default, chained and matched model,
    with Dutot, Carli, harmonic, CSWD and unit value available. With
    quantities or expenditure: Laspeyres, Paasche, Fisher, Törnqvist, Walsh,
    Marshall-Edgeworth, Lowe, Young and the geometric forms. Analyst-defined
    formulae run in a restricted evaluator.
  - The price, weight and index reference periods are kept apart.
  - Aggregation is weighted through COICOP 2018 (all 871 codes) or the
    organisation's own tree, with contributions that add up.
  - Primary and secondary disclosure control apply to every published table.
- **Quality change**: the matched model by default; a ledger of approved
  explicit adjustments (overlap, direct comparison, quantity, option cost,
  imputation, hedonic); an impact report per entry.
- **Missing prices, seasonal items, outliers and revisions**:
  - five imputation methods, with the imputed share reported;
  - class confinement, weight update, Rothwell and counter-seasonal
    treatments;
  - seasonal adjustment by X-13ARIMA-SEATS or STL, named on every output;
  - four outlier screens, with no automatic exclusion and a reason required
    for every decision;
  - revision triangles and bias tests on registered vintages.
- **Scanner and transaction data**: GEKS-Fisher, GEKS-Törnqvist, time product
  dummy (weighted and unweighted), time dummy hedonic and Geary-Khamis, with
  six window-extension rules. The spread across methods is reported.
- **Macro analysis**:
  - rates, contributions (including Ribe across a chain link) and core
    measures;
  - base effects, diffusion and dispersion;
  - deflation, real wages, constant prices, and PPP conversion with price
    level indices.
- **Other index families**:
  - spatial parities (CPD and Geary-Khamis, including from Eurostat's
    published PPPs);
  - import and export price and unit value indices, and the terms of trade;
  - construction input cost and output price indices;
  - contract escalation;
  - residential property price indices (stratified median, mix-adjusted
    mean, repeat sales, SPAR, hedonic), including from HM Land Registry
    price paid data;
  - rents and owner-occupied housing.
- **Uncertainty**:
  - design-based bootstrap intervals when a sampling design is declared, and
    a refusal when none is;
  - a methodological sensitivity range, labelled a lower bound, never
    combined with an interval;
  - on every headline, its interval or a statement that it has none.
- **Forecasts and scenarios**:
  - ARIMA, SARIMAX, ETS, and pass-through and Phillips-curve regressions,
    each backtested against a naive benchmark and its interval checked
    against its measured error;
  - scenarios with sourced assumptions.
  - Neither can be exported without its interval, backtest, benchmark
    comparison and assumptions.
- **Governance**:
  - role-based access and password authentication;
  - a hash-chained audit log;
  - a run registry that reproduces any registered run, with approval and
    corrections as vintages;
  - one provenance stamp on every export, readable back from any of them;
  - immutable raw layers with replayable transformation logs;
  - eight connectors to agency data (ONS, Eurostat, IMF, World Bank, OECD,
    BLS, FAO, generic SDMX) with caching and stale-if-error.
- **Outputs**: slide deck, Word and Markdown reports, the Excel evidence
  pack, the PDF statistical bulletin, CSV and SDMX-ML 2.1 (validated against
  the standard's schemas).

### Release close-out
- **Wiring audit** repeated after Phases 5–9b (`docs/wiring_audit.md`): 69
  modules. Seven wired gaps were closed, each with a page-level test:
  - withdrawing an outlier decision;
  - the correction audit event;
  - reproducing a registered forecast or scenario;
  - replaying the characteristics layers;
  - Eurostat's published PPPs on the Spatial page;
  - price level indices;
  - a user-defined classification tree.

  Dead code was removed.
- **Docker**: `.dockerignore` rules now apply at every depth (194 stale
  bytecode files would have entered the image). The package source is
  copied before the editable install. The image's `/app` was reproduced from
  the build context, and the build's lint, type-check and test steps were run
  there. That caught a test that passed only because of a git-ignored local
  database. It also caught `run_pipeline` creating an empty SQLite file
  whenever it ran without a database; both are fixed. The image itself has
  never been built: there is no container engine on the development machine
  (`docs/release_verification.md`).
- **Version**: 1.0.0 in both `pyproject.toml` and `pricelab.__version__`,
  which had disagreed (0.2.0 against 0.1.0) and which every provenance stamp
  records.
- **Documents**:
  - `docs/methodology_statement.md`: how the platform compiles an index, for
    a statistician who will not read the code;
  - `docs/limitations_register.md`: every known limitation, by what it
    affects;
  - `docs/release_verification.md`: what was and was not verified, and the
    X-13 decision.

### Release follow-up
- **Provenance carries the commit.** Every registered run and every
  provenance stamp records:
  - the full git commit of the package's own checkout;
  - a `-dirty` suffix when the working tree differed from it, so a run from
    uncommitted work says so;
  - where there is no checkout, the image's `PRICELAB_CODE_VERSION` build
    value, or a stated `unknown`.

  A run from a clean tree traces to exactly one commit. The Audit page's
  verification reports whether the code reproducing a run is the
  registering commit. Before this, the record was the short hash of
  whatever directory the process started in, with no dirty flag.
- **Claims corrected.** The methodology statement, the user guide and the
  revision note no longer say that reproduction replays a run "byte for
  byte" or "from exactly that record". It re-runs the stored input and
  configuration with the code running now. They also no longer say that
  deleting any audit record breaks the chain; deleting the most recent
  records, or emptying the log, does not.
- **Demonstration seeding and banner** (`core/demo.py`):
  - one viewer account from `PRICELAB_DEMO_USERNAME`/`_PASSWORD`, created
    only in a database with no users, and never any other role;
  - a demonstration run of the bundled collection;
  - a banner on every page, and on sign-in: uploads are not retained, and
    the audit log and run registry reset on restart.

  The administrator guide now says Community Cloud is for demonstration
  only.
- **X-13ARIMA-SEATS gated.** It runs only when an administrator sets
  `PRICELAB_X13_ENABLED`, not merely because the binary is installed. With
  the setting on, every output labels it an unvalidated path until an
  integration test against a published official adjustment exists.
- The environment fingerprint now includes scipy, statsmodels and pyarrow.

### Known limitations
See `docs/limitations_register.md`. The main ones for anyone relying on a
published number:
- X-13ARIMA-SEATS has never run against the real program; it is off unless
  an administrator enables it, and every seasonally adjusted series so far is
  STL's, and says so.
- No sampling interval exists without a declared design.
- The Docker image and compose stack are unbuilt.
- CI has not yet run on any of these commits.

## Phase 9b: forecasting and scenarios (2026-09-24)

### Added
- **`engine/forecasting.py`**: ARIMA (order by KPSS and AICc), SARIMAX, ETS,
  and pass-through and Phillips-curve regressions. Every forecast is:
  - backtested from rolling origins against the random walk, or the seasonal
    naive for a seasonal series, on the same window, and says in its label
    whether it beats it (lower RMSE and a Diebold-Mariano rejection);
  - carrying both its model-implied interval and the interval its backtest
    errors imply, and saying where the second is wider;
  - correlational wherever a regression coefficient appears.
- **`engine/scenarios.py`**: shocks to energy prices, the exchange rate,
  wages and administered prices. Each has a stated size, timing, phase-in
  and a coefficient with its source (stated, the run's weights, or a
  pass-through regression). They are applied to a stated baseline rule, with
  a 50/80/95% fan from the rule's past errors. A scenario is never labelled a
  forecast.
- **`engine/projection.py`** and **`reporting/projections.py`**: the four
  parts every projection carries, and the only export path (CSV, Excel,
  Markdown), which refuses a projection missing any. It holds the export
  inventory and the scan tests.
- **Registry**: `ProjectionRunORM` (migration 0008); forecasts and scenarios
  are registered against a run and reproduced with their backtests checked
  digest for digest.
- **Charts**: `forecast_chart` and `scenario_chart`; `mark(...,
  projection=...)` and the rule that a forecast and a scenario share an axis
  or a table only when each is labelled as what it is.
- **Pages**: Forecasts and Scenarios.
- **Sensitivity**: labelled a lower bound everywhere it appears; the imputed
  share of the aggregate beside the table.

### Fixed
- `docs/methodology/sensitivity.md` said the fixture's spread was across 13
  alternatives; 12 are computed.

## Phase 9a: uncertainty, variance and sensitivity (2026-09-23)

### Added
- **Property methods on real transactions**: `data/price_paid.py` reads HM
  Land Registry price paid data as published (Open Government Licence) and
  applies stated, counted rules; the Property prices page takes a file
  directly. Run on all 930,559 records for 2024; a verbatim Oldham extract is
  the test fixture.
- **`engine/uncertainty.py`**: design-based bootstrap confidence intervals
  for index movements -- whole clusters resampled within strata (Rao-Wu,
  n_h - 1) -- for a declared design only. With no design declared, no
  interval, and the reason. On a known population the 95% interval covered
  the truth in 95.5% of 400 samples; the naive quote-level interval in
  65.5%, and was 2.06 times narrower.
- **`engine/sensitivity.py`**: the headline recompiled under each
  defensible alternative formula, aggregation, multilateral method and
  window, quality adjustment treatment, imputation method and seasonal
  treatment, with the range and the settings at each end named, and every
  alternative the data cannot support listed with the reason.
- **The Uncertainty page**, with sampling uncertainty and methodological
  sensitivity in two sections and two charts.
- **Every analyst-view headline carries its uncertainty or says it has
  none**: `pages/common.HEADLINE_SURFACES` lists the pages, each calls
  `show_uncertainty`, and a scan fails on any page that shows a headline
  without being listed.
- **`charts.mark(..., band=...)`**: a confidence interval and a sensitivity
  range on one axis are refused by `check_axes`.
- **`docs/methodology/`**: uncertainty and sensitivity notes (26 in total);
  the property note gains the real-transaction findings.

### Fixed
- The loader took a headerless file's first record as its column names; it
  now recognises a headerless file.
- The loader's sample could end inside a quoted field, failing the whole
  upload ("EOF inside string"); the sample is cut to its last complete line.
- `compare_methods` failed outright when one method could not be estimated;
  a method the data cannot support is now reported with the reason and the
  others still run.
- The hedonic VIF ran one auxiliary regression per regressor, which did not
  finish on a year of transactions with a dummy per county; it is now the
  diagonal of the inverse correlation matrix -- identical values -- and the
  leverage one matrix product.
- Five page-test fixtures depended on another module having imported the
  audit table first; each now registers its tables.

### Notes
- On the bundled collection the sensitivity range is 118.12 (overall-mean
  imputation) to 138.57 (the Carli formula) around the published 135.60.
- On 2024's real transactions: hedonic 102.73, stratified median 102.46,
  mix-adjusted mean 99.21, repeat sales 125.75 -- a single year's re-sales
  are selected on quick resales.

## Phase 8: asset and property price indices (2026-09-23)

### Added
- **`engine/asset.py`**: residential property price indices by stratified
  median, mix-adjusted mean, repeat sales (Bailey-Muth-Nourse and
  Case-Shiller weighted), sale price appraisal ratio and hedonic (through
  `engine/hedonic.fit_hedonic`). Every result states what it measures and its
  principal limitation; `compare_methods` runs all of them on the same sales
  and explains the gaps with numbers (the change in the quality mix of what
  sold, within strata and within cells, against each method's gap).
- **Repeat sales revisions** through `engine/revision.py`: the index as it
  would have been published at the end of each period, as vintages, in the
  same triangle as every other revision; the Revisions page shows them.
- **Diagnostics and suppression**: sales per stratum per period, the share of
  sales each method uses and of a stock sold, and the per-stratum table under
  `suppress_with_secondary`.
- **`engine/housing.py`**: a matched-rent price index, and owner-occupied
  housing as four questions -- rental equivalence, net acquisitions (with
  land excluded on request), user cost (which may go negative, and says so)
  and payments (which refuses capital repayment).
- **Contributions in the Excel evidence pack**, on their own sheet, with the
  tree level stated and the residual as a row; the test that pins the sheet
  list now names it.
- **Real data**: spatial parities from Eurostat's PPPs by analytical
  category (`eurostat.ppp_comparison_inputs`), and published house price
  totals rebuilt from their new- and existing-dwelling parts
  (`eurostat.hpi_components`); recorded fixtures of both.
- **New pages**: Property prices, and Rents and owner-occupied housing.
- **`docs/methodology/`**: asset and housing notes (24 in total); the spatial
  note gains the real-data finding.

### Changed
- The spatial module now withholds a region with no chain of shared products
  to the base -- reporting it with the reason -- instead of refusing the whole
  comparison. Real data forced this: Japan, the United States and the United
  Kingdom publish only aggregate PPPs and share no detailed category with
  anyone.
- The Revisions page's display is a shared function (`show_analysis`), used
  for registry corrections and repeat sales alike.

### Notes
- Spatial real-data check: weighted CPD over the 15 detailed categories with
  expenditure weights, against Eurostat's published PPPs for actual
  individual consumption: median gap 5.5%, systematic (Spearman −0.77 with
  the price level index), because the categories cover about half of
  consumption and leave out the non-traded services cheapest in low-price
  countries.
- House price real-data check: published totals rebuilt from their parts to
  within 0.061 index points (Germany) and under 0.02 elsewhere.
- Repeat sales revision magnitude on the demonstration market: mean absolute
  revision 0.42 index points.

## Phase 7b: spatial, trade, construction and contract escalation (2026-09-23)

### Added
- **Contributions across a chain link**: `decomposition.ribe_contributions`,
  the published treatment (OECD, "OECD calculation of contributions to
  overall annual inflation", 2018/2022, section 3, after Walschots 2016; the
  "Ribe" contribution of the HICP Methodological Manual, chapter 8; used for
  Eurostat's published HICP contributions). Tested on a hand-derived case and
  against Eurostat's published contributions to euro-area inflation for all
  twelve divisions and months of 2025: largest difference 0.0056 pp, the
  rounding of the two-decimal publication. On the Decomposition page for the
  published HICP.
- **Contributions in the release**: the bulletin, the Word and Markdown
  reports and the deck carry a "Contributions to the change" table
  (`report.contributions_summary`) with the level of the tree stated and the
  residual against the published headline change as a row, not absorbed. A
  run without weights gets the reason instead.
- **`engine/spatial.py`**: CPD (weighted or not, with standard errors) and
  Geary-Khamis parities, the matched-product count for every region pair, a
  thin-overlap rule that reports a region's estimate but withholds it from
  the published parities and from conversion, and price level indices.
- **`engine/trade.py`**: import and export price indices (Fisher, Laspeyres
  or Paasche over products), unit value indices, the unit value bias
  (`unit_value_bias`), and terms of trade. Every unit value index carries the
  bias and the conditions under which it is defensible.
- **`engine/construction.py`**: the input cost index and the output price
  index (a fixed bill of quantities at tender rates), with the difference
  between them stated on every result, and their ratio reported as the
  implied margin and productivity movement.
- **`engine/escalation.py`**: indexation clauses with lag, averaging, dead
  band (excess or full), trigger, indexed share, cap and collar, producing a
  payment schedule and a plain-language summary naming the index, its
  vintage, the lag, and every period where a limit changed the payment.
- **New pages**: Spatial comparison, Trade prices, Construction and Contract
  escalation, each with page-level AppTests; the escalation page will not
  build a schedule on an uploaded index until its vintage is stated.
- **`docs/methodology/`**: spatial, trade, construction and escalation notes
  (22 in total); the decomposition note gains the chain-link and release
  sections.
- A fixture recorded from Eurostat's published HICP contributions
  (`prc_hicp_ctrb`, euro area, divisions, 2025).

### Notes
- The unit value bias, demonstrated: two products at unchanged prices of 10
  and 100 a tonne, with the tonnage shifting from 90/10 to 10/90. Every price
  index gives 100; the unit value index gives 91/19 × 100 = 478.95, a gap of
  378.95 index points.

## Phase 7a: decomposition, core measures and deflation (2026-09-23)

### Added
- **`engine/decomposition.py`**: period-on-period, year-on-year, annualised,
  three-months-on-three and cumulative rates; **contributions at every level
  of the classification tree**, built on `engine/aggregation`, reconciling to
  the headline to eight decimal places with the residual reported;
  **core measures** — exclusion based (a parent node excludes everything
  beneath it), trimmed mean at a configurable trim, weighted median,
  variance weighted, sticky price — each stating its parameters and data
  requirements, with `core_measure_availability` saying why a measure the
  data cannot support is not offered; **base effects** split exactly into
  carry-over and impulse, and the change in the rate into this period's
  movement and the base effect; **diffusion** and **dispersion**.
- **`engine/deflation.py`**: deflation with explicit alignment (a monthly
  series and a quarterly deflator raise; conversion is the user's call via
  `to_frequency`, recorded with the result); real wages and income; exact
  real growth beside the approximation; constant prices and volume indices;
  PPP conversion and price level indices. Every result names its deflator,
  reference period and alignment.
- **Real agency data**: the Eurostat connector can fetch several series in
  one request by key path (`eurostat.hicp_key`) and `eurostat.hicp_tree`
  builds the HICP's three-level tree the way it is compiled within a year.
  Recorded euro-area responses (55 series, 2022-12 to 2025-12, with the item
  weights) are the fixtures the contribution tests run on; the re-aggregated
  headline matches the published one to within 0.004 index points.
- **The chart rule**: every artist in `reporting/charts.py` declares its
  unit, and `check_figure` refuses an axis that mixes an index level, a
  percentage change or a percentage-point contribution, or that puts nominal
  and real together without saying which is which. New charts:
  `seasonal_adjustment_chart`, `contributions_chart`, `rates_chart`,
  `deflation_chart`.
- **`reporting/exports.SEASONAL_SURFACES`**: every surface an adjusted series
  reaches, enumerated in code and checked by `tests/test_seasonal_carried.py`,
  which also fails when a module handles the adjusted series without being
  enumerated.
- **New pages**: Decomposition (the compiled run or the published HICP) and
  Deflation, each with page-level AppTests.
- **`docs/methodology/`**: decomposition and deflation notes (eighteen in
  total); the seasonal note gains the surface table, the null-seasonality
  tolerance and the direct-adjustment section.

### Changed
- `SeasonalAdjustment.label` now states that the adjustment was **direct** and
  that seasonally adjusted components need not sum to an adjusted total. The
  constrained adjustment is labelled, not implemented.
- The Seasonality page draws the shared seasonal adjustment chart (engine in
  the title, legend and note) instead of an unlabelled line chart; the chart
  is also in the Word report, the deck and the bulletin.
- `parse_jsonstat` returns a column for each dimension that varies across the
  response, so a multi-series request can be told apart. A single-series
  response decodes exactly as before.

### Fixed
- The SDMX-ML message carried the seasonally adjusted series with nothing
  saying what adjusted it: the CSV's `basis` column never reached it. A
  `BASIS` series attribute now carries the engine and the direct-adjustment
  statement (and the multilateral method on those series). Found by the
  surface inventory test.

### Notes
- X-13ARIMA-SEATS is still not installed on this machine; every seasonal
  surface was verified on the STL fallback, and the X-13 path remains
  exercised through a substituted runner.
- The null-seasonality tolerance is the series' own noise, sigma: the
  adjustment's root-mean-square change must not exceed it. Over 40 seeds of
  ten years the measured change was 0.62 sigma (median) and 0.77 sigma
  (worst); on four years the worst case reached 1.13 sigma.

## Phase 6: seasonality, outliers and revision control (2026-09-22)

### Added
- **`engine/seasonal.py`**: strictly seasonal item detection (a season is a
  repetition, so an item merely interrupted is excluded with the count that
  disqualified it); **class confinement** and **weight update**, compiled on
  one shared aggregator so the gap between them is the weights and nothing
  else, and reported rather than chosen silently; the **Rothwell index**;
  **counter-seasonal estimation** of off-season prices, marked
  `counter_seasonal` so an estimate never reads as an observation; and
  **seasonal adjustment** by X-13ARIMA-SEATS where the binary is present and
  STL otherwise.
- **The engine that ran is named in every output.** `SeasonalAdjustment.label`
  names it unconditionally and says so when it is the fallback, and it
  appears on the page before the chart, in the chart caption, on the CSV's
  first line, in the method note, the Word report, the deck, the Excel pack's
  new "Series basis" sheet and the bulletin's "How each series was produced".
  `adjustment_engine="x13"` raises rather than substituting.
- **The unadjusted series travels with the adjusted one.** Both live on one
  object and are emitted by one loop in `reporting/exports.additional_series`,
  so no format can ship one without the other.
- **A stability test**: the seasonal factors re-estimated across sub-samples,
  plus the gap between the adjusted and unadjusted annualised trends — which
  seasonal factors that average out over a year cannot move, so any material
  value there is the adjustment rather than the prices. Factors are
  normalised to average to one over every full year, without which STL's
  seasonal component carries a drift of its own.
- **`engine/outliers.py`**: Tukey fences, the quartile method,
  Hidiroglou-Berthelot and a period-on-period ratio screen over price
  relatives, with a configurable deadband (5%) without which screening the
  bundled collection flags 23% of all relatives rather than 1.8%. All four run
  by default because they disagree, and the queue records which caught each
  quote.
- **A review queue that is the only way a quote leaves the index.** Detection
  changes nothing; an unreviewed flag excludes nothing; a reason is required
  by the widget, the model, the ledger function and a `NOT NULL` column; a
  rejected quote is *marked*, never dropped, and carries the analyst and the
  reason; every decision reaches the audit log. Exclusions are reported as a
  share of the quotes they would have fed, in the units imputation already
  uses. New `outlier_decisions` table (migration 0007) and
  `core/ledger.py` functions, keyed by content hash like the quality
  adjustment ledger.
- **`engine/revision.py`**: revision triangles, mean and mean absolute
  revision, a t-test for bias reported with its sample size, and published
  against current for any reference period — all built on the registry's own
  vintages, with `core.registry.vintage_chain` walking the supersedes chain
  in both directions so entering at the latest vintage cannot silently hide
  the revisions.
- **Task 0, closing the multilateral side car**:
  `multilateral.build_multilateral_all` rolls a per-category multilateral
  series up to an all-items headline through `engine/aggregation`, naming any
  category that could not produce one rather than dropping it;
  `multilateral.seasonal_multilateral` adds the year-over-year monthly and
  rolling-year forms; and every multilateral level now appears in the
  publication table, CSV, SDMX, Markdown, Word, deck, Excel pack and bulletin
  beside the method, window, splice rule and the spread the rule could have
  moved it by.
- **New pages**: Outliers, Seasonality and Revisions, plus a headline roll-up
  on Multilateral. Each has page-level AppTest coverage.
- **New config sections** on `RunConfig`: `seasonal`, `outlier` and
  `revision`, all disabled by default, so a config saved before them loads
  and compiles exactly as before with no schema version bump.
- **`docs/methodology/`**: seasonal, outliers and revision notes (sixteen in
  total).

### Changed
- **mypy strict now covers `reporting/`** as well as core, engine and data.
  python-pptx, python-docx and reportlab are made opaque by a per-module
  override rather than half-typed: they ship partial annotations, so every
  call into them otherwise reads as "call to untyped function in typed
  context" and strict mode fails on code that is itself fully typed.

### Fixed
- The Seasonality page used `str.capitalize` on the engine label, which
  lowercased "X-13ARIMA-SEATS" into "x-13arima-seats" — the one string on
  that page that has to survive verbatim. Caught by the page test that looks
  for the engine name.
- `reporting/exports.to_sdmx_ml` read its observation value off an
  `itertuples` field named `index`, which silently shadows `tuple.index`. It
  worked, and it is exactly the coincidence that stops working.
- The Docker build runs the suite but never copied the bundled workbook
  (`.dockerignore` excluded every `*.xlsx`), so the Phase 3 hard gate skipped
  inside the image. The workbook is now copied and allowed through, and the
  registry and page tests in `test_revision.py` skip rather than error when
  it is absent, like the seasonal and outlier modules.

### Notes
- X-13ARIMA-SEATS is **not installed on this machine**, so every local run
  falls back to STL and is labelled as doing so. The X-13 path is exercised
  through a substituted runner (`_run_x13` is a module-level function for
  that reason), because a fallback whose alternative has never been executed
  is an assumption rather than a branch.
- Not done: X-11/SEATS quality diagnostics on the STL path; trading-day and
  moving-holiday adjustment; additively consistent adjustment across an
  aggregation structure; revision analysis by horizon or by source; selective
  editing that ranks flags by their effect on the aggregate.

## Phase 5: multilateral methods (2026-09-22)

### Added
- **`engine/multilateral.py`**: GEKS with Fisher or Törnqvist as the
  bilateral block (the latter is CCDI), the time product dummy and its
  expenditure-weighted form, the time dummy hedonic (fitted through
  `engine/hedonic.py`, not reimplemented) and Geary-Khamis solved
  iteratively for quality-adjusted unit values. GEKS is computed in logs as
  one level per period, so transitivity is exact rather than approximate;
  the regressions are sparse least squares, so a hundred thousand
  transactions over a twenty-five month window takes under a second.
- **Window extension**: movement, window, half and mean splice, FBEW and
  FBMW, with a configurable window length (25 periods by default) and a
  configurable anchor month for the two fixed-base rules. All six are one
  implementation over the same set of candidate links, because that is what
  the literature says they are.
- **`splice_spread_pp`**: per published period, the gap between the highest
  and lowest level the period could have taken had the link been made
  elsewhere in the overlap — the size of the judgement the rule made,
  rather than a revision measured at the splice point, which is zero by
  construction for whichever rule was used.
- **The comparison view** (`method_comparison`, `comparison_spread`, and the
  new **Multilateral** page): the same collection under every method, window
  and rule, with the spread reported in index points of the final level and
  percentage points of the annualised rate. A method this data cannot
  support is listed with its reason, never dropped.
- **`drift_against_chained`**: the chained bilateral index scored against
  the transitive one through the same `engine/splicing.chain_drift` the
  bilateral engine already uses — a multilateral series *is* the direct
  comparison.
- **`MultilateralConfig`** on `RunConfig` (method, window, splice, anchor
  month, minimum matched items), so two runs differing only in their splice
  hash differently. Disabled by default; a config saved before this phase
  loads unchanged, with no schema version bump.
- **`scripts/generate_scanner_data.py`** and the fixtures it writes
  (`tests/fixtures/scanner_transactions.csv`, `scanner_characteristics.csv`):
  30 monthly periods, 63% of the period × product grid empty, promotions
  with an asymmetric quantity response, a spine of staples so the window
  stays connected, and a known quality gradient for the hedonic to recover.
- **`docs/methodology/multilateral.md`**: the thirteenth methodology note.

### Notes
- Appendix 2's chain drift test now passes on data where prices and
  quantities return exactly to their starting values after twelve periods: a
  chained Törnqvist drifts by more than twenty index points, every
  multilateral method returns to exactly 100, and the diagnostic reports the
  magnitude.
- On the scanner fixture the chained Törnqvist ends more than ten index
  points above every multilateral series, and the methods disagree among
  themselves by several points — which is the point of the comparison view.
- **Fixed**: `engine/hedonic.py`'s multicollinearity warning read "severe
  multicollinearity: none above threshold" when only the design condition
  number tripped. It now names whichever test fired, with its threshold —
  found because the Multilateral page puts hedonic warnings in front of the
  reader.
- Not done: a per-category window or method, a multilateral series rolled up
  through the classification tree, seasonal multilateral variants, and
  standard errors on a multilateral level.

## Quantity and expenditure ingestion (2026-09-21)

### Added
- **Canonical schema** carries optional `quantity`, `expenditure` and `unit`
  (`core/config.Schema`, `core/models.PriceQuote`, `PRICE_QUOTE_COLUMNS`);
  the column mapping suggests them, the upload standardises and checks them
  (negative values are structural errors), and because the raw layer,
  transformation log, vintage stamp and registry hash are built from the
  whole frame and the whole configuration, the fields are traceable from
  the first commit that carries them.
- **Expenditure consistency check** (`data/validation.expenditure_inconsistencies`,
  `QualityConfig.expenditure_tolerance`, 1% by default): rows where
  expenditure differs from price x quantity are a high-severity consistency
  finding, carried in the run as `expenditure_check` and listed on the
  Quality page; neither figure is preferred.
- **Quantity-weighted formulae on the interface**: Paasche, Fisher,
  Törnqvist, Walsh, Marshall-Edgeworth, geometric Laspeyres and Paasche and
  the unit value index are selectable on Ingest (`engine.index.QUANTITY_FORMULAE`,
  `formula_availability`, `quantity_series`); unavailable ones are labelled
  with the reason rather than hidden. Laspeyres uses the quantity basket
  when quantities exist and the Young form otherwise. Quantity derived from
  expenditure / price is flagged as derived. The unit value index is gated
  behind `IndexConfig.homogeneity_justification`, required and recorded.
- **Tests on ingested data** (`tests/test_quantity_ingestion.py`, fixture
  `tests/fixtures/quantity_panel.csv`): Laspeyres > Fisher > Paasche
  ordering and substitution bias every period, Fisher = sqrt(L x P), factor
  reversal per category, all formulae compile and the superlatives agree;
  page-level AppTest for upload with quantities -> Fisher -> result, for the
  unit value assertion, for the price-only labels and for the inconsistent
  expenditure warning and table.

### Changed
- `engine.auto.infer_schema` no longer reads an expenditure column as the
  weight; it maps quantity, expenditure and unit and excludes them from the
  price guess. `data.upload.read_price_data` infers the schema when none is
  given.
- The method note states which Laspeyres form was used and where the
  quantities came from.

## Phase 10.5 — wiring audit and production verification (`a1ee8a4`, `8accbf1`, `9524b2e`, `ed3682d`, 2026-09-21)
- Every library path unreachable from the product wired with a page-level
  test (`tests/test_wiring.py`); the suite runs against PostgreSQL as well as
  SQLite (`tests/dbtarget.py`); the bulletin rendered to images and fixed;
  a cold start on a clean checkout and the administrator guide corrected by
  it. Details in `docs/backlog.md`.

## Phase 10 — reporting, provenance and production hardening (`05d6bdf`, 2026-09-21)

### Added
- **Provenance stamp** (`core/provenance.py`): one function builds the run
  identifier, data vintage, code commit, complete parameters, suppression
  rules, non-standard-formula flag, quality-adjustment count, headline and
  timestamp; every export carries it and `reporting/readback.py` reads it
  back out of each format.
- **Excel evidence pack** (`reporting/excel.py`): Provenance, Source data,
  Weights, Elementary aggregates, Upper level aggregates, Quality adjustment
  ledger, Final index, Methodology log, Audit extract; every string sanitised,
  suppressed cells labelled with their rule.
- **PDF statistical bulletin** (`reporting/bulletin.py`): headline read from
  the registry (and refused if the live result disagrees), key points,
  charts, tables, methodology note, revision statement, contact and release
  block, provenance page.
- **Machine-readable output** (`reporting/exports.py`): stamped CSV of the
  publication table and of the cleaned data; SDMX-ML 2.1 GenericData
  validated against the SDMX TWG's schema set (vendored under
  `tests/fixtures/sdmx_2_1`).
- **Disclosure control on every published table**: primary suppression below
  the configured quote count and secondary suppression against the all-items
  aggregate, shown as "suppressed" with the rule, never blank.
- **Registry** records the headline figure and the data vintage at
  registration (migration 0006).
- **Upload provenance**: every price upload goes through the immutable raw
  Parquet layer with a vintage receipt; every compile writes the cleaned
  layer and its transformation log; the log's replay now includes the
  quality-adjustment link step. Characteristics files for the hedonic module
  pass through the same four properties (raw layer, transformation log,
  validation, vintage), and the vintage travels into every hedonic
  adjustment, the ledger and the registry hash.
- **Hardening**: connection pooling and statement timeouts (SQLite abort via
  progress handler; PostgreSQL `statement_timeout`); stale-if-error
  degradation for external connectors with the age stated; per-user upload
  rate limiting before bytes are read; structured JSON logging with one
  correlation id per run on every line and in the audit event; liveness and
  readiness probes (CLI and HTTP), used by the Docker healthcheck; backup and
  restore of the database and the store with a hashed manifest, tested by a
  restore that reproduces an identical index.
- **Documentation**: methodology note per engine module, user guide by user
  type, administrator guide, onboarding tutorial, this changelog.
- The skipped superlative golden value is now a hand-calculated two-good,
  two-period proof with the arithmetic in the test.

### Changed
- `pyarrow` pinned below 25 with the reason in `pyproject.toml`.
- Word report tables, Markdown tables, deck text runs and PDF text go through
  the export sanitiser; the injection sweep covers every format and headers.
- Docker and compose healthchecks use the readiness probe.

## Phase 4 — quality adjustment and hedonics (`21c47e3`, 2026-09-21)
- `engine/quality_adjustment.py`: overlap, direct comparison, quantity,
  option cost, class/targeted/overall mean (and link-to-show-no-change,
  warning); adjustments in price terms and index points with reason codes;
  the ledger as configuration; `apply_adjustments`; the impact report.
- `engine/hedonic.py`: log-linear, semi-log, Box-Cox; time-dummy,
  characteristics-price and imputation variants; WLS; full diagnostics;
  multicollinearity warning.
- `quality_adjustments` table (migration 0005), the Quality adjustment page,
  impact section in report and deck. Golden values from CPI Manual 2020
  Chapter 6.

## Phases 2 and 3 (`5f37a14`, 2026-09-21)
- Phase 2: connectors (ONS, Eurostat, IMF, World Bank, OECD, BLS, FAO,
  generic SDMX) with retry, caching, schema validation and vintage stamps;
  loaders, column mapping with confirmation, validation with audited
  overrides, full COICOP 2018 tree, immutable raw/cleaned Parquet store.
- Phase 3: elementary (harmonic mean, CSWD, unit value) and bilateral
  (Paasche, Fisher, Törnqvist, Walsh, Marshall-Edgeworth, Lowe, Young,
  geometric forms) indices; aggregation with additive contributions;
  splicing and chain drift; targeted/overall mean imputation with response
  rates; custom formulae; Hypothesis axiom tests; Chapter 8 golden values.
- Review fixes: an absent `index_reference_period` is refused; an
  uncomputable one gives an all-NaN series; unweighted leaves are reported;
  `price_update_shares` refuses rather than degrading.

## Phase 1 and patches (`5adc466`, `25d53df`, `99dc576`, 2026-09-20)
- Pydantic configuration with schema versioning; role-based access;
  username/password auth with Argon2 and idle timeout; export sanitisation;
  restricted formula evaluator; small-cell suppression; hash-chained audit
  log; run registry with approval and correction vintages; bounded cache;
  Alembic migrations; multi-page navigation. Three reference periods with
  unconditional rebasing; migration healing; suppression coverage guard.

## Phase 0 (`167ec54`, 2026-09-20)
- Engine reorganised into `core/`, `engine/`, `data/`, `reporting/` with no
  behavioural change.

## Before the platform plan (2026-09-07 to 2026-09-08)
- Initial PriceLab: diagnosis, cleaning, imputation and matched-model
  indexing of a supermarket price collection, with deck and report exports;
  performance fixes; eight review findings fixed; CI; MIT licence.
