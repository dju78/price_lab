# Backlog: PriceLab -> governed price index platform

Agreed scope (2026-09-20): the "minimum credible release" cut, evolving this
repository in place rather than starting a new one, targeting SQLite plus a
simple username/password auth fallback so the app keeps deploying to
Streamlit Community Cloud. The full enterprise specification this backlog is
drawn from covers ten phases and several index domains PriceLab does not
touch (multilateral scanner-data methods, asset/property indices, trade and
construction indices, deflation and spatial comparison, forecasting); those
stay out of scope until asked for.

## Phase 0 - Migration scaffold (done)

Moved the existing engine into `core/` / `engine/` / `data/` / `reporting/`
subpackages with no behavioural change. `pricelab/__init__.py` still
re-exports the same public names, so `app.py` needed no changes. All 50
existing tests pass unchanged; import paths in `tests/test_pricelab.py`
were updated to the new module locations.

## Phase 1 - Foundation and governance

Pydantic models for the objects already implied by `core/config.py` and by
the flag/imputation columns already threaded through `engine/`; role-based
access control (administrator, compiler, analyst, viewer) with a server-side
`require_role` check; an append-only, hash-chained audit log persisted to
SQLite; a run registry that hashes input data plus the full parameter set so
a published figure can be reproduced byte for byte; CSV export sanitisation
against spreadsheet formula injection (the gap noted in the Phase 0 audit at
`app.py`'s cleaned-data and flagged-observations downloads); `pyproject.toml`
with ruff and mypy strict; multi-page `st.navigation` routing in place of the
current single-page `app.py`.

## Phase 2 - Data layer

Formalise `data/upload.py`'s schema inference and validation into a proper
loader/connector split; add read-only connectors for the official series a
user is actually likely to compare against (ONS, and a generic SDMX 2.1
connector others can be configured from); an immutable raw layer plus a
cleaned layer with a replayable transformation log, so the cleaned data can
always be regenerated from source rather than only being an in-memory
DataFrame.

## Phase 3 - Core engine extension

Fix the conflation of price-reference, weight-reference and index-reference
periods in `engine/index.py`'s single `base_period`; add Paasche, Fisher,
Tornqvist, Walsh, Marshall-Edgeworth, Lowe and Young to the existing
Jevons/Dutot/Carli/Laspeyres set; add the harmonic mean and CSWD elementary
formulae; add the Appendix 2 golden-value and axiom property tests (time
reversal, factor reversal, additivity) alongside the existing axiomatic test
style in `tests/test_pricelab.py`.

## Phase 4 - Quality adjustment and hedonics

New. `engine/quality.py` today only repairs unit-of-measurement (order-of-100)
errors; this phase adds overlap pricing, direct and explicit-quantity
comparison, and hedonic regression (time dummy and characteristics-price
variants) for genuine item replacement, plus the quality-adjustment ledger
and impact report the master specification calls for.

## Phase 10 - Reporting and hardening

Extend the existing PPTX/DOCX builders (`reporting/deck.py`,
`reporting/report.py`) with a PDF bulletin and run-identifier/vintage/code-
version stamping on every export; close the disclosure-control gap (small-
cell suppression) before any client-uploaded internal data reaches this
platform.

## Deferred (not in the agreed scope; revisit if asked)

Multilateral methods (GEKS, TPD, Geary-Khamis) for scanner/transaction data;
seasonal adjustment via X-13ARIMA-SEATS/STL beyond the current seasonal-hold
imputation; outlier review queue (today's scale-error detection is automatic,
not a reviewable queue); revision vintages and bootstrap/variance uncertainty;
decomposition (contributions, core inflation measures, base effects,
diffusion); deflation, real values, PPP and spatial price levels; asset,
trade and construction indices; forecasting and scenario tooling; PostgreSQL
and OIDC (would require hosting beyond Streamlit Community Cloud).
