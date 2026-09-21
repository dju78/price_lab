# Changelog

All notable changes to PriceLab. Phases refer to the platform build plan in
`docs/backlog.md`; each phase is one commit and passed the same hard gate
(every existing test green, the bundled fixture's index series identical to
its committed baseline, ruff and mypy strict at zero).

## Unreleased — Phase 10: reporting, provenance and production hardening (2026-09-21)

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
