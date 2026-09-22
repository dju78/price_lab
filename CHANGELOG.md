# Changelog

All notable changes to PriceLab. Phases refer to the platform build plan in
`docs/backlog.md`; each phase is one commit and passed the same hard gate
(every existing test green, the bundled fixture's index series identical to
its committed baseline, ruff and mypy strict at zero).

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
