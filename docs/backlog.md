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
COICOP divisions were seeded at this point (the full tree followed in Phase
2). There is no in-app user-management page; accounts are
provisioned with `scripts/create_user.py`. Secondary suppression handles one
published total per group, not a cascading multi-total solver.

## Phase 2 - Data layer, connectors and validation (done)

Delivered: `BaseConnector`, with retry/backoff and jitter, `Retry-After`-aware
rate-limit handling, response caching (`core.cache`, TTL support added),
schema validation before the engine ever sees a response, a vintage stamp
(source, query, retrieval time, response hash) and an audit event on every
fetch; seven concrete connectors (ONS, Eurostat, IMF, World Bank, OECD, BLS,
FAO) plus a generic SDMX 2.1 connector, each tested against a real, recorded
fixture for the happy path, a timeout, a rate limit, a malformed payload and
a schema change, with no network access in the test suite. `data/loaders.py`
formalises the upload pipeline: CSV/Excel (incl. multi-sheet)/Parquet/JSON
dispatch, encoding and delimiter detection (a counting vote across candidate
delimiters, not `csv.Sniffer` alone, which gives up on the whole sample the
moment one line -- a title row -- doesn't contain the real delimiter),
header-row inference, and a memory-footprint estimate enforced against
`pages.ingest.validate_upload`'s existing size cap, with a chunked CSV
reader that aborts mid-read on real measured memory, not only a
pre-read projection. `data/mapping.py` adds confidence-scored column-mapping
suggestions (built on the existing `infer_schema`, not a second matching
engine) with mandatory analyst confirmation, persisted per file content hash
so the same file maps identically next time; wired into `pages/ingest.py` as
a real blocking gate, not just a backend module. `data/validation.py` adds
completeness, timeliness and conformity dimensions (none existed before),
reuses `data.upload.validate` for consistency/uniqueness/validity and
`engine.quality.classify_missing` for the missingness-mechanism half of
plausibility, and blocks compiling on any critical finding until an analyst
accepts, excludes, corrects or justifies it -- also wired into `pages/
ingest.py` as a real gate, with the decision persisted (`validation_overrides`)
and audited. The full COICOP 2018 tree (871 codes, divisions through
sub-classes) is seeded from the UN Stats structure file, replacing the
13-division stub; CPA, NACE and HS were not seeded -- no authoritative,
machine-readable source for any of them was found and verified in time --
but the loader that would take one (`load_classification_from_csv`,
`load_user_defined_tree`) is generic and tested against a synthetic tree,
along with weight-sum validation at every node. `data/store.py` adds an
immutable raw Parquet layer, a cleaned layer, and a transformation log
proven (against the real fixture, byte-for-byte on disk) to replay the
cleaned layer exactly from the raw layer. Two new migrations (0003 for the
full COICOP tree, 0004 for `validation_overrides` and `column_mappings`);
276 tests added on top of Phase 1's 161 (298 passing, the one strict xfail
still xfailing); ruff and mypy strict stay at zero errors, mypy's scope
extended to `data/`. The real fixture's index series is proven identical,
value-for-value, whether reached through the old direct-`infer_schema` path
or the new mapping-confirmation-gated path.

## Phase 1 patch - reference periods, test gaps, suppression guard (done)

`IndexConfig` now has `price_reference_period`, `weight_reference_period`
and `index_reference_period`, replacing the single, conflated `base_period`
(kept as a deprecated alias). `engine.index.build_index` reads
`price_reference_period` for a fixed-base comparison's denominator and
`index_reference_period` for a chained index's rebasing step; only these
two change any actual computed value, and only when explicitly set, so
every existing run's output is unchanged. `weight_reference_period` is
carried and displayed but read by no formula yet -- Lowe and Young are
still Phase 3. A legacy (schema_version 1) config upconverts its three
fields from `base_period` on load and is flagged (`RunConfig.
legacy_upconverted`); `core.registry.reproduce` logs that upconversion to
the audit trail rather than reinterpreting an old run's parameters
silently. `IndexRunORM` gained matching columns in migration 0001 itself
(no production data existed yet to need a second migration for). Also:
`core.security.suppress_with_secondary` now refuses (`SuppressionCoverageError`)
rather than silently running an incomplete pass when asked to protect more
than one grouping dimension at once; an AppTest-driven test proves a viewer
session has no route to a compiler-only page at all, landing on Reports
instead, rather than only testing that the `require_role` decorator raises;
and upload validation (`pages.ingest.validate_upload`) is a pure, directly
tested function checked before `getvalue()` is ever called.

## Phase 1 patch addendum - both branches, migration safety, remaining gaps (done)

Six corrections to the patch above, all in the same "no new index
mathematics" spirit:

1. **Rebasing now applies to both compilation methods, not only chained.**
   `build_index` previously gated its rebase-to-`index_reference_period`
   step on `cfg.chained`, so a fixed-base index silently had its index
   reference period forced to equal its price reference period -- the
   `base_period` conflation surviving in one branch after the rest of it
   was split out. Rebasing is now an unconditional final step applied to
   whichever series was produced. For the default case (both references
   defaulting to the same period) this is a proven no-op; a fixed-base
   index can now genuinely be compiled against one period and published
   reading 100 at a different one (`tests/test_reference_periods.py`).
   Noted in passing, not fixed here (out of this addendum's scope): a
   pre-existing quirk in `build_index`'s fixed-base branch hardcodes the
   very first period's level to `base_value` regardless of
   `price_reference_period`, correct only when the price reference
   happens to be the series' first period (true of every case reachable
   through the interface today). Worth fixing alongside Lowe/Young in
   Phase 3, when `price_reference_period` first becomes independently
   user-set.
2. **Migration safety.** Editing 0001 in place, as the previous patch did,
   left any database that had already applied the pre-edit 0001 with
   `alembic_version` recording "0001" while lacking the three new columns
   -- and Alembic tracks revisions by ID, not content, so it would never
   re-run 0001 to notice. Chose the additive-migration fix over a
   startup schema check: migration 0002 inspects the live `index_runs`
   table and adds only whichever of the three columns is actually
   missing, so it is a no-op against a database created fresh from the
   edited 0001 and a real repair against one that ran the original.
   `tests/test_migrations.py` proves both paths, including that an
   existing row survives the healing.
3. **Parameter hash coverage: was already correct.** Both
   `core.registry.register_run`'s content hash and
   `core.cache.content_key` are computed from `RunConfig.to_json()`,
   which serialises every real field including the three reference
   periods; nothing needed fixing. Tests added anyway
   (`tests/test_reference_periods.py`) to keep it that way against a
   future field gaining `exclude=True` by mistake.
4. **`weight_reference_period` after `price_reference_period` is now
   rejected**, not merely carried: a pydantic validator on `IndexConfig`
   raises rather than warns, on the reasoning that there is no legitimate
   case for it (a fixed-basket index's weights cannot come from later
   than the prices they weight) and a warning is easy to miss outside an
   interactive session.
5. **Upconversion audit coverage extended to the interface layer.**
   `core.registry.reproduce` already logged `LEGACY_CONFIG_UPCONVERTED`;
   `pages.common.load_registered_run` now wraps it so the one interface
   path that can load a legacy config also surfaces the fact to the
   person on screen (`st.warning`), not only to the audit log, and is
   itself directly tested rather than only the lower-level registry
   function.
6. **The deck/chart label bug is now a Phase 3 entry condition, not a
   backlog line.** `tests/test_deferred_deck_label.py` is a `strict=True`
   xfail: it fails today, documenting that `reporting/charts.py`'s
   `index_chart` and `reporting/deck.py`'s stat callout hardcode their
   "= 100" label to the series' first period rather than
   `index_reference_period`. `strict=True` means an accidental pass (the
   label happening to look right without actually being fixed) fails the
   suite. Delete the marker in the same commit that fixes it -- the deck
   is the client-facing artefact.

## Phase 3 - The elementary and bilateral index engine (done)

Both entry conditions cleared first. The `strict=True` xfail in
`tests/test_deferred_deck_label.py` is gone: the "= 100" label now reads
`engine.index.resolve_index_reference_period`, one function shared by the
rebasing arithmetic and by every label describing it (chart y-axis, deck
headline stat, report method note, Findings metric, Index build panel), so
a label can no longer name a different period from the one the series was
rebased to. And an unset `index_reference_period` now defaults to
`price_reference_period` rather than to the first observation.

That second change turned out not to close the base_value quirk on its
own, contrary to the note it inherited: with a later price reference, the
old rebasing divided by a level that was itself the hardcoded 100, so the
fabricated first row survived the change. The hardcode is therefore gone
too -- a fixed-base index now computes every period against the price
reference, including periods before it, and only the price reference
period itself is assigned `base_value` by definition. A chained index's
first period is still the starting level, which is what it genuinely is.

Delivered: `engine/elementary.py` (harmonic mean, CSWD, unit value added
to the existing Jevons/Dutot/Carli, which are called rather than
reimplemented; every result carries its sample size, imputation count,
formula and parameters; unit value refuses to run without an explicit,
justified homogeneity assertion, because its failure mode -- reporting a
shift in purchase mix as a price change -- is invisible in the output).
`engine/bilateral.py` (Paasche, Fisher, Tornqvist, Walsh,
Marshall-Edgeworth, Lowe, Young, geometric Laspeyres and Paasche, plus the
Fisher quantity index so factor reversal can be tested rather than
asserted; `price_updating_effect` computes Lowe and Young over the same
data and attributes the gap between them to price updating, which is what
that gap is). `engine/aggregation.py` (weighted roll-up through the Phase
2 classification tree, parent weights derived by summing children,
contributions that sum to the headline change exactly, weight-hierarchy
problems reported rather than absorbed; the equally weighted geometric
aggregate migrated out of `build_all`, which still calls it). 
`engine/splicing.py` (rebasing, link factors, splicing, chaining, price
updating, and a chain drift diagnostic with a configurable threshold).
Two imputation methods (targeted cell mean and overall mean, both
anchored to the last observed price rather than cascading like
`class_mean`) plus response-rate tracking that reports imputed values as a
share of the aggregate they feed. `engine/custom.py` wires the Phase 1
restricted AST evaluator into the interface: a compiler can define an
elementary or aggregate formula, it is parsed by the whitelist walker and
never by `eval`, it is rejected when written rather than mid-compile, it
rides in the config JSON so the registry hash and cache key already cover
it, and a run using one is marked non-standard on the deck, in the written
report, in the docx and at the head of every CSV export.

Scope decisions worth knowing about: `engine.index.laspeyres` is untouched
and still falls back to Jevons with no weights, because every run compiled
through the interface goes through it; note that what it computes is the
Young form (a weighted mean of relatives), not the quantity-basket
Laspeyres in `engine/bilateral.py`, and the two coincide only when the
weights are the base period's own expenditure shares. The new bilateral
formulae are library-level: they are not yet selectable from the Ingest
page, which still offers the four elementary formulae plus the custom
escape hatch, because choosing one requires quantity data the upload
schema does not yet carry. Golden values come from CPI Manual 2020 Chapter
8 Tables 8.1-8.3 and match at the manual's own published precision (one
decimal for indices); no superlative golden values are asserted, because
the 2020 volume contains no reproducible worked example of one -- see
`tests/test_golden_values.py` for the full finding.

## Phase 3 review (done, 2026-09-21)

Phase 3 was left uncommitted when the previous session hit its usage limit
during final verification. Reviewed against the phase prompt and the hard
gate; three silent-failure findings fixed before committing: `build_index`
skipped the rebase without comment when an explicit `index_reference_period`
was absent from the data (now refused, like an absent price reference) or
had no computable level (now an all-NaN series, since raising would take
down `diagnostics.chain_drift`, which builds a direct variant of every run
and had been subtracting an un-rebased direct series from a rebased chained
one on thin categories); `aggregate_tree` dropped a leaf with an index but
no weight without listing it in `problems`; `price_update_shares` fell back
to un-updated shares when no update ratio was computable, which made
`price_updating_effect` report a Lowe equal to Young and an effect of
exactly zero. Also found: this machine's Windows Smart App Control now
refuses pyarrow 25.0.1's `_fs.pyd`; the venv is on 24.0.0 (no code or
`pyproject` change; CI on Linux is unaffected).

## Phase 4 - Quality adjustment and hedonics (done)

Delivered: `engine/quality_adjustment.py` -- overlap pricing, direct
comparison, explicit quantity adjustment, option cost, class mean, targeted
mean and overall mean imputation (plus link-to-show-no-change, which exists
in the world and warns on use), each returning a `QualityAdjustment` with
the quality ratio, the adjusted price, the adjustment in price terms and in
index points (closed forms for Jevons and Carli, sum-based for Dutot) and a
reason code; `apply_adjustments`, which links an approved replacement onto
the old item's series before imputation and indexing, flags every linked
row (`quality_adjustment_flag`, `replacement_flag`, `replaced_item_id`) and
logs every row it moved or dropped; and `impact_report`, which re-runs the
pipeline as configured, with every replacement linked at ratio 1, and with
none linked, and states the adjustments' effect in index points and in
percentage points of annual inflation (year on year where the span allows,
annualised over the span otherwise, and it says which), attributed per
entry by leave-one-out with the interaction residual reported as its own
row so the column sums exactly. `engine/hedonic.py` -- log-linear, semi-log
and Box-Cox (lambda by maximum likelihood on the regression) functional
forms; time-dummy (pooled, index read from the dummies, with and without
the Kennedy bias correction), characteristics-price (per-period fits,
Laspeyres-, Paasche- and Fisher-type bundle pricing) and imputation (single
and double) variants; dummy-encoded categoricals; WLS with expenditure
weights; and the diagnostics the specification names: adjusted R squared,
HC1 robust standard errors, VIF per regressor and the design's condition
number, residual and leverage series (charted in `reporting/charts.py`),
coefficient stability across rolling windows, and k-fold out-of-sample
error. Severe multicollinearity raises `HedonicMulticollinearityWarning`,
is recorded on the result and travels with any adjustment read from it.

The ledger is configuration: `RunConfig.quality_adjustment.entries` holds
every approved valuation (old item, new item, period, method, ratio,
parameters, justification, approver), so the registry's content hash, the
cache key, `reproduce()` and every saved config already cover it with no
second mechanism; the approval record behind it is the `quality_adjustments`
table (migration 0005, `core/ledger.py`), keyed by the input data's content
hash so a re-uploaded collection brings its approved replacements back, with
withdrawals kept as rows rather than deleted. `pages/quality_adjustment.py`
(compiler and administrator) lists replacement candidates from the
collection's exits and entrants, values one by any method with the result
shown before approval, approves it (persisted, audited, recompiled), shows
the ledger with withdrawal, the impact report, and fits a hedonic model
from an uploaded characteristics file with its diagnostics on screen. The
impact report is in the Markdown and Word reports and on its own slide in
the deck; the method note's "Limitations" paragraph now says correctly that
the matched-model default attributes a replacement's price gap to quality,
not price.

Acceptance: the hedonic estimator recovers a known quality effect within 2
percent on a synthetic panel and the impact report attributes it; switching
a replacement's method changes the headline and the change is attributed
exactly (per entry plus residual); a specification with a duplicated
regressor warns rather than reporting. Golden values from the CPI Manual
2020 Chapter 6 text (equation 6.4's 1.023765; the targeted-mean chain 6.26,
6.32, 6.34; Table 6.4a's 1.12; Table 6.5's unit prices; the option-cost
1.01942) reproduce exactly. Not asserted, because the published text does
not carry the inputs: Table 6.1's full tableau (so Table 6.2's whole chain)
and Table 6.6's washing-machine regression. 54 tests added (513 passing,
the one strict xfail-turned-skip unchanged); engine/ coverage 94%; ruff and
mypy strict at zero, statsmodels and scipy added as dependencies.

Scope decisions worth knowing about: the pack names the module
`engine/quality.py`, but that name has meant value-level data quality
(sentinel recoding, fault repair) since Phase 0 and is imported from six
modules, so quality adjustment lives in `engine/quality_adjustment.py` and
`engine/hedonic.py` rather than renaming the existing module under the hard
gate. Characteristics are not part of the upload schema: the hedonic model
takes a separate characteristics file (item_id plus columns) on the page.
The adjustment is applied to the incoming series ("current period
adjustment", dividing the replacement's prices by the ratio) rather than to
the reference price; in a chained short-term index the relatives are the
same, and it keeps `price_reported` untouched. A run loaded from the
registry cannot have its ledger changed from the page -- that is a
correction, and goes through `correct_run`.

## Phase 10 - Reporting, provenance and production hardening (done)

Task 0a, characteristics provenance -- what the upload already satisfied:
**none of the four.** The hedonic characteristics file was read straight
from the widget into a DataFrame: no raw Parquet layer, no transformation
log, no validation, no vintage stamp. And, found while checking: the price
upload did not pass through the raw layer or the transformation log in the
*interface* either -- `data/store.py` existed as a tested library that
`pages/ingest.py` never called (validation and the content hash were
applied; the vintage stamp existed only for connectors). Both now go
through the same path. Prices: `store.record_upload` writes the raw layer
once per content hash with a `.vintage.json` receipt (file, SHA-256 of the
bytes as received, actor, time) and `compile_and_store` writes the cleaned
layer and its transformation log after every compile; the log's replay
now includes the quality-adjustment link step, matching the pipeline's
order. Characteristics: `validation.assess_characteristics` (uniqueness of
`item_id` critical; completeness, validity, conformity against the priced
items), the raw layer and receipt, `standardise_characteristics` with its
steps logged and `replay_characteristics` proving the replay, and the
vintage set on the fit (`HedonicResult.data_vintage`) so it rides in the
parameters of every hedonic adjustment, hence in the ledger, the config
JSON and the registry hash. Task 0b: the superlative skip is a proof --
two hand-calculated two-good, two-period examples with the arithmetic in
the test (one unit-elastic where every superlative coincides, one where
they differ).

Provenance (Task 4) first, because everything else carries it:
`core/provenance.py` builds one `ProvenanceStamp` (run id or
"unregistered", data vintage = input content hash, source and receipt,
git commit, PriceLab version, environment fingerprint, the full config,
suppression rules, non-standard flag and expression, ledger count,
headline, vintage/supersedes/correction/approval, timestamp);
`reporting/readback.py` reads it back out of CSV (comment line), Markdown
(fenced block), docx (tagged paragraph -- core properties cap at 255
characters), pptx (provenance slide's notes), xlsx (Provenance sheet),
PDF (keywords and text), SDMX (dataset annotation). The registry gained
the headline and data-vintage columns (migration 0006) so a bulletin
reads its number from the registry. Excel evidence pack (Task 1): nine
sheets as specified, written through `sanitize_dataframe` headers included
and stored as values, suppressed cells labelled with their rule. PDF
bulletin (Task 2, reportlab): headline from `IndexRunORM.headline_value`,
refused if the live result disagrees; key points, charts, tables,
methodology note, revision statement from the registry's vintage chain,
contact and release block from settings, provenance page. Machine-readable
(Task 3): `publication_table` applies primary and secondary suppression to
every published table (secondary because the equally weighted geometric
aggregate would otherwise give a lone suppressed cell away); stamped CSV;
SDMX-ML 2.1 GenericData **validated against the standard's own schema set**
-- the SDMX TWG's 59 XSDs (1.1 MB) vendored under `tests/fixtures/sdmx_2_1`
via `sdmx1.install_schemas`, with a test that the validator rejects a
broken message. Hardening (Task 5): pooling and `pool_pre_ping` for server
databases, PostgreSQL `statement_timeout`, and for SQLite a busy timeout
plus a per-statement deadline enforced through a progress handler (a
runaway statement is interrupted in a test); stale-if-error on
`BaseConnector.fetch` -- the last good response is kept without a lifetime
and served on any `ConnectorError` with `stale=True`, its age and the
reason, audited as `served_stale` (a test simulates the outage); per-user
sliding-window rate limit on upload before `getvalue()`; session timeout
proven end to end (an idle token lands on the sign-in form and is
deleted); stdlib JSON logging with a `ContextVar` correlation id bound by
`run_pipeline`, on every line and in the `CALCULATION_RUN` audit event;
liveness and readiness probes (database answers and is at migration head,
store writable) as CLI, HTTP server and the Docker healthcheck; backup
(SQLite online backup API + store copy + hashed manifest) and restore
(verify, refuse non-empty targets) with the test that destroys and
restores and reproduces an identical index. `pyarrow>=14.0,<25` with the
reason. Documentation (Task 6): `docs/methodology/` (twelve notes),
`docs/user_guide.md` by the five user types, `docs/admin_guide.md`,
`docs/tutorial.md`, `CHANGELOG.md`.

Also fixed on the way: Word report tables, the Markdown index-levels and
ledger tables, and deck text runs were not routed through the export
sanitiser -- found by the new injection sweep, which now covers every
format and headers.

Not verified directly: the PostgreSQL pool and `statement_timeout` paths
are configured but this environment has no PostgreSQL to run them
against; PDF pages were checked by text extraction, not rendered (no
poppler on this machine). 551 tests; engine/ coverage 95%; ruff and mypy
strict at zero.

## Phase 10.5 - Wiring audit and production verification (done, 2026-09-21)

A static reachability analysis from the pages (plus manual checks) over
every module in core/, engine/, data/ and reporting/ found the rest of the
store.py class -- tested libraries the product never called -- and each was
wired with a page-level AppTest test (tests/test_wiring.py): data.loaders
behind Ingest (encoding, delimiter, header-row inference, Parquet/JSON,
memory cap); the three reference periods, which had no control anywhere in
the interface; models.validate_frame as the boundary and stage contract;
non-critical validation findings, never displayed, and conformity, never
checked; the weighted aggregate, contributions and tree roll-up; the
Lowe/Young price-updating report; custom aggregate formulae; response
rates; the thresholded chain-drift diagnostic; the hedonic imputation
variant and characteristics-price index; registry corrections; an Audit
page (chain verification, export identification, run reproduction and
replay); a Sources page for the eight connectors with their cache,
stale-if-error degradation and audit events. Dead code removed: six
pydantic mirrors never instantiated, a dead re-export. Found on the way:
numeric-looking category codes were read as integers and failed the
contract.

PostgreSQL: tests/dbtarget.py runs every database fixture against
PRICELAB_TEST_DATABASE_URL when set; against a local PostgreSQL 15 the
whole suite passed with no test behaving differently (only the
SQLite-only backup tests skip, by design). docker-compose now runs
PostgreSQL by default with a `tests` profile; CI runs both backends.

Rendered the bulletin to images (pypdfium2, local only) and fixed what
text extraction could not see: a legend over the data, greyscale-
indistinguishable lines, repeated tick labels, a table over the margin
and wrapping mid-word, a notice separated from its table, a duplicated
paragraph, orphaned headings, clipped provenance values.

Cold start on a clean checkout forced these corrections into the
administrator guide: activate the environment (or prefix commands);
`python -m pip`, not a `pip` executable; a short checkout path on Windows
(pyarrow's headers exceed the path limit); the password prompt needs a
terminal, so `--password-stdin` was added for unattended provisioning;
Streamlit's first-run email prompt blocked `streamlit run` until the
repository config made the server headless; a restore needs everything
holding the database stopped, probe server included, and the scripts now
refuse with a message rather than a traceback; `backup.py` refuses
PostgreSQL outright (the guide had said it still copied the store).

## Deferred (not in the agreed scope; revisit if asked)

Multilateral methods (GEKS, TPD, Geary-Khamis) for scanner/transaction data;
seasonal adjustment via X-13ARIMA-SEATS/STL beyond the current seasonal-hold
imputation; outlier review queue (today's scale-error detection is automatic,
not a reviewable queue); revision vintages and bootstrap/variance uncertainty;
decomposition (contributions, core inflation measures, base effects,
diffusion); deflation, real values, PPP and spatial price levels; asset,
trade and construction indices; forecasting and scenario tooling; PostgreSQL
and OIDC (would require hosting beyond Streamlit Community Cloud); an in-app
user-management page; CPA, NACE and HS classification reference data (no
authoritative, machine-readable source verified yet -- the generic loader
that would take one already exists); a cascading (multi-total)
secondary-suppression solver.
