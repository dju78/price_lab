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

## Phase 1 - Foundation and governance (done)

Delivered: pydantic domain schemas (`core/models.py`) plus a DataFrame
column-contract check that samples rows rather than re-validating a whole
panel; `RunConfig` migrated from dataclasses to pydantic v2 with a
`schema_version` field and a migration path, round-trip tested against a
config JSON in the pre-Phase-1 dataclass shape; a `Settings` object separate
from `RunConfig`, sourced from `PRICELAB_`-prefixed environment variables;
role-based access control (administrator, compiler, analyst, viewer) via
`core.security.require_role`, checked from inside each page's own function
body and unit-tested by calling a decorated function directly under a
mismatched role; username/password authentication with Argon2 hashing and an
idle-timeout session token, structured behind an `AuthProvider` protocol so
OIDC can be a second implementation later; CSV export sanitisation
(`core.security.safe_csv`) covering both cell values and column/row index
labels (a pivoted export turns a category name into a header cell too),
wired into every download in `pages/`; a restricted arithmetic formula
evaluator built ahead of Phase 3's need for one, rejecting everything but
numeric literals, arithmetic operators, whitelisted names and whitelisted
function calls; small-cell suppression with primary and secondary
(complementary) suppression for the single-published-total case; an
append-only, hash-chained audit log (`core/audit.py`) with `verify_chain()`;
a run registry (`core/registry.py`) hashing input data and the full
parameter set, recording the git commit and a library-version fingerprint,
supporting `approve_run`/`correct_run` (new vintage, mandatory reason,
original left untouched) and `reproduce()`; a bounded, content-hash-keyed
analysis cache (`core/cache.py`) replacing the previous unbounded
`st.cache_resource`, which never holds a matplotlib Figure; upload size and
extension checks enforced before `getvalue()` reads the file into memory;
SQLAlchemy models, one Alembic migration covering users, sessions, audit
events, index runs and the classification tree, seeded with the thirteen
COICOP 2018 divisions; `app.py` converted to an authenticated,
role-filtered `st.navigation` with the original single-page flow's content
moved (not rewritten) into `pages/ingest|quality|imputation|index_build|
findings|diagnostics|reports.py`; `pyproject.toml` replacing
`requirements.txt`, with ruff and mypy strict scoped to `core/` and
`engine/` at zero errors, pre-commit hooks, and CI running lint, type
check and the full suite.

Scope decisions worth knowing about: role gating puts Ingest/Quality/
Imputation/Index build behind compiler+administrator, adds analyst to
Findings/Diagnostics, and opens Reports to every role including viewer, so
an analyst or viewer with nothing of their own compiled can load a
previously approved run there via the registry instead. Only the top-level
COICOP divisions are seeded, not the full tree (see README's Known
limitations). There is no in-app user-management page; accounts are
provisioned with `scripts/create_user.py`. Secondary suppression handles one
published total per group, not a cascading multi-total solver.

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
version stamping on every export, now that `core/registry.py` actually has
that information to stamp with.

## Deferred (not in the agreed scope; revisit if asked)

Multilateral methods (GEKS, TPD, Geary-Khamis) for scanner/transaction data;
seasonal adjustment via X-13ARIMA-SEATS/STL beyond the current seasonal-hold
imputation; outlier review queue (today's scale-error detection is automatic,
not a reviewable queue); revision vintages and bootstrap/variance uncertainty;
decomposition (contributions, core inflation measures, base effects,
diffusion); deflation, real values, PPP and spatial price levels; asset,
trade and construction indices; forecasting and scenario tooling; PostgreSQL
and OIDC (would require hosting beyond Streamlit Community Cloud); an in-app
user-management page; the full COICOP 2018 tree below division level; a
cascading (multi-total) secondary-suppression solver.
