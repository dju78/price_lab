# Changelog

All notable changes to PriceLab. Phases refer to the platform build plan in
`docs/backlog.md`; each phase is one commit and passed the same hard gate
(every existing test green, the bundled fixture's index series identical to
its committed baseline, ruff and mypy strict at zero).

## Unreleased — Phase 7b: spatial, trade, construction and contract escalation (2026-09-23)

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

## Unreleased — Phase 7a: decomposition, core measures and deflation (2026-09-23)

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

## Unreleased — Phase 6: seasonality, outliers and revision control (2026-09-22)

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

## Unreleased — Phase 5: multilateral methods (2026-09-22)

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

## Unreleased — Quantity and expenditure ingestion (2026-09-21)

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
