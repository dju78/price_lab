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
non-critical validation findings, listed under the automatic decisions
but without conformity, which was never checked; the weighted aggregate, contributions and tree roll-up; the
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

## Quantity and expenditure ingestion (done, 2026-09-21)

The canonical schema now carries optional quantity, expenditure and unit
(config Schema, PriceQuote, PRICE_QUOTE_COLUMNS); mapping suggests them,
standardise coerces and keeps them, validate rejects negatives, and the
raw layer, transformation log, vintage stamp and registry hash carry them
because they are built from the whole frame and the whole configuration
(tests prove each). Where both quantity and expenditure are present the
validation engine checks expenditure ~= price x quantity to
QualityConfig.expenditure_tolerance (1%) and reports the offending rows
as a high consistency finding, carried in the run as expenditure_check
and listed on the Quality page; the index is unchanged by them and neither
figure is preferred. Expenditure alone derives quantity as expenditure /
price and the series is named quantity_derived so every result says so.

engine.index gained QUANTITY_FORMULAE, quantity_series,
formula_availability and a _bilateral_link adapter that routes the period
loop to engine.bilateral (or elementary.unit_value) -- the tested
implementations, no new formulae. Laspeyres is the quantity basket with
quantities and the Young form without. The Ingest selector lists every
formula and labels the unavailable ones with the reason; unit value needs
the homogeneity assertion, enforced in IndexConfig and on the page and
recorded with the run. The method note names the form and the quantity
source.

tests/test_quantity_ingestion.py (14 tests) on the generated fixture
tests/fixtures/quantity_panel.csv: price-only fixture unchanged against
the Phase 3 baseline; L > F > P and substitution bias every period;
Fisher = sqrt(L x P); factor reversal per category; every formula
compiles; provenance in each layer; AppTest upload -> Fisher -> result,
unit value assertion, price-only labels, inconsistent expenditure on
Ingest and Quality. Suite green on SQLite (582 passed, 1 skipped) and
PostgreSQL (579 passed, 4 skipped); engine coverage 95%.

Not done: `unit` is carried and shown but not used to reject a unit value
index over mixed units (the assertion is the compiler's, as the manual
puts it); a per-category homogeneity assertion; quantities in the Excel
pack's Weights sheet beyond the source-data sheet.

## Phase 5 - Multilateral methods for transaction and scanner data (done, 2026-09-22)

`engine/multilateral.py` delivers the six methods the phase asks for.
GEKS takes Fisher or Tornqvist as its bilateral block (`engine.bilateral`,
the tested implementations, not reimplemented) and is computed in logs as
one level per period -- `g(t) = mean over bridges of ln P(l,t)`, with the
comparison between any two periods a difference of two fixed numbers -- so
transitivity holds to machine precision and a test asserts it rather than
approximating it. The time product dummy and its expenditure-weighted form
are sparse least squares (`scipy.sparse.linalg.lsqr`), because a dense
design over a real window is tens of thousands of columns; the time dummy
hedonic is an adapter over `engine.hedonic`'s `time_dummy` variant and its
`time_dummy_index`, so the regression, the dummy encoding, the robust
standard errors and the Kennedy correction are the ones already tested;
Geary-Khamis iterates reference prices and period levels to a tolerance and
reports its iteration count, returning its last iterate with a warning
rather than vanishing from a comparison view if it does not settle.

Window extension delivers movement, window, half and mean splice, FBEW and
FBMW, with a configurable window (25 by default) and anchor month. The four
rolling rules are one implementation over the same dictionary of candidate
links, differing only in which of them is taken, because that is what they
are; FBEW and FBMW are the two ends of the same candidate set over an
expanding window from the anchor. `splice_spread_pp` reports the gap between
the highest and lowest candidate per period, which is the size of the
judgement the rule made -- a "revision against the previous window" measured
at the splice point is zero by construction for whichever rule was used and
would have flattered every choice equally.

The comparison view (`method_comparison`, `comparison_spread`, and the
Multilateral page) computes every method on every window under every rule
and reports the spread in index points and in points of annualised rate,
listing a method this data cannot support with its reason rather than
dropping it. `drift_against_chained` scores the chained bilateral against
the transitive series through the same `engine.splicing.chain_drift` the
bilateral engine uses. `MultilateralConfig` on `RunConfig` carries method,
window, splice, anchor month and minimum matched items, so two runs
differing only in their splice hash differently; disabled by default, and a
config saved before the phase loads unchanged with no schema version bump.

Acceptance: Appendix 2's test 5 passes on a panel whose prices and
quantities return exactly to their starting values after twelve periods --
a chained Tornqvist drifts by more than twenty index points, every
multilateral method returns to exactly 100, and the diagnostic reports the
magnitude. On `tests/fixtures/scanner_transactions.csv` (30 periods, 63% of
the period x product grid empty, promotions with an asymmetric quantity
response, a spine of staples so the window stays connected) the chained
Tornqvist ends more than ten points above every multilateral series. The
six splices reproduce their published relationships on a worked example
where no window disagrees with the last: all six give one identical series,
equal to the direct index; on churning data mean splice lies between the
single-point rules, as its definition requires. A hundred thousand
transactions over a twenty-five month window complete in under a second per
method against the thirty the criterion allows.

Found on the way: GEKS's matched-model requirement bites harder than
expected on a fixture with no long-lived products at all -- no period can
bridge to every other and the method is genuinely undefined. The engine
says so and names the time product dummy as the alternative; the fixture
grew a spine of staples, which is what real transaction data has.

84 new tests (`tests/test_multilateral.py`); 667 in the suite on SQLite
(663 passed and 4 skipped on PostgreSQL, the SQLite-only backup tests, as
before); engine coverage 95%, `multilateral.py` 95%.

Also fixed on the way, because the Multilateral page surfaces hedonic
warnings to the reader: the multicollinearity warning read "severe
multicollinearity: none above threshold" whenever only the design condition
number tripped, contradicting itself in its first clause and leaving the
reader unable to tell which diagnostic to go and look at. It now names
whichever test fired, with the thresholds.

Not done: a per-category window length or method; a multilateral series
rolled up through the classification tree (the weighted aggregate still
operates on bilateral category indices); seasonal multilateral variants
(a year-over-year window, the seasonal GEKS forms); standard errors on a
multilateral level; and the multilateral series is not yet carried into the
report, the deck or the publication export.

## Phase 6 - Seasonality, outliers and revision control (done, 2026-09-22)

**Task 0, closing the multilateral side car.**
`multilateral.build_multilateral_all` compiles a multilateral series per
category and rolls it up through `engine.aggregation` -- the same weighted
mean and the same classification-tree path the bilateral headline uses, not a
second implementation. A category whose data cannot support the method is
named in `skipped` with the reason rather than dropped, because a category
missing from a weighted headline moves the headline. What the aggregation
does not preserve is transitivity, and the method note says so.
`multilateral.seasonal_multilateral` adds the year-over-year monthly and
rolling-year forms (each calendar month gets its own multilateral sub-index,
so seasonality never enters the comparison), which is Phase 5's orphan folded
in here. Carriage into the outputs is done in one place: `additional_series`
appends the multilateral and adjusted series to `publication_table`, which is
the single function every format reads its rows from, and each row carries a
`basis` column -- so the CSV, SDMX, Excel pack, bulletin and wide table got
them without opting in, and `reporting/report.supplementary_tables` does the
same job for the narrative formats. A test walks the Markdown, Word, deck,
Excel, CSV and SDMX outputs looking for the method beside the level.

**Task 1, seasonality.** `engine/seasonal.py`. Strictly seasonal items are
detected by repetition, not by absence: an item missing three months once is a
collection failure and is excluded with the count that disqualified it, which
is checkable rather than asserted. Class confinement and weight update are
both compiled and the gap reported. Two decisions make that comparison mean
something: both treatments use the same aggregator, differing only in the
weights handed to it, and weight update's weights are built by *subtraction*
from confinement's -- only an item outside its own observed season loses its
weight. Computing them as "what has a price this month" would also subtract
non-response and churn, and the two treatments would then differ by several
index points on a collection with no seasonal item in it; the test that
strips the seasonal items and asserts the two series are identical to machine
precision is what holds that honest. The chained weighted mean of category
*relatives* is used rather than a mean of levels, because a mean of levels
under moving weights moves when the weights do and no price does.

Also delivered: the Rothwell index (base-year average prices as the only
denominator a seasonal item has, with the item count reported because the
composition effect is inherent); counter-seasonal estimation, which moves an
off-season price with the in-season items rather than holding it flat, every
estimate labelled a construction; and seasonal adjustment by X-13ARIMA-SEATS
or STL.

The two hard rules are enforced in code rather than documented.
`SeasonalAdjustment.label` names the engine unconditionally and says when it
is the fallback, and it is printed by the page (before the chart), the chart
caption, the CSV's first line, the method note, the Word report, the deck,
the Excel pack's "Series basis" sheet and the bulletin.
`adjustment_engine = "x13"` raises rather than substituting. The unadjusted
series lives on the same object as the adjusted one and both are emitted by
one loop, so no format can ship one without the other. Stability is tested by
re-estimating the factors across sub-samples and by comparing the adjusted
and unadjusted annualised trends -- factors that average out over a year
cannot move a trend, so a gap there is the adjustment. Getting that gap to
zero needed the factors normalised to average to one over every full year;
STL does not constrain its seasonal component and it was carrying a drift of
its own.

**Task 2, outliers.** `engine/outliers.py`: Tukey fences and the quartile
method on log relatives, Hidiroglou-Berthelot with its importance exponent,
and a fixed-band ratio screen for the thin cells the other three cannot run
on. All four by default, because they disagree and the queue shows which
caught what. A deadband was necessary and is a stated parameter: without one,
screening the bundled collection flags 23% of all price relatives, which is
not a queue, it is the data; at 5% it is 1.8%.

No code path removes a quote. Detection returns flags and changes nothing; an
unreviewed flag excludes nothing; a reason is required by the widget, by
`OutlierDecision`, by `record_outlier_decision` and by a NOT NULL column
(migration 0007); a rejected quote keeps its row and gains the analyst, the
reason and an excluded flag, with its price set to unavailable so the index
cannot use it; accepted and annotated quotes are marked too; and every
decision reaches the audit log. Re-deciding withdraws the previous record
rather than overwriting it. Exclusions are reported as a share of the quotes
they would have fed, in the same shape `response_rates` reports imputation.
The stage sits between the quality adjustment ledger and imputation, so the
hole an exclusion leaves is filled by the run's own imputation.

**Task 3, revisions.** `engine/revision.py` on the registry's own vintages,
with no parallel store: a vintage is a registered run and reproduces byte for
byte. `core.registry.vintage_chain` walks the supersedes chain in both
directions, because entering it at the latest vintage and getting only that
vintage back is the natural way to write a revision analysis that ignores
every revision. Triangle, MR, MAR, a t-test for bias reported with its sample
size (and refusing a verdict below three observations), and published against
current. The end-to-end acceptance criterion is a page-level test: register
and approve on Reports, register a correction with a reason, then open
Revisions and read the change off the triangle with the original still
approved and still reproducing beside it.

**Also in this phase.** mypy strict extended to `reporting/` (131 errors
fixed, mostly missing annotations); python-pptx, python-docx and reportlab
made opaque by a per-module override rather than half-typed, because they
ship partial annotations and every call into them otherwise fails strict mode
on code that is itself fully typed. Three new pages (Outliers, Seasonality,
Revisions) and a headline roll-up on Multilateral, each with AppTest
coverage. Three new config sections, all disabled by default, so a config
saved before them loads unchanged with no version bump.

Found on the way: the Seasonality page used `str.capitalize` on the engine
label, lowercasing "X-13ARIMA-SEATS" into "x-13arima-seats" -- the one string
on that page that must survive verbatim, caught by the page test that looks
for the engine name. And `to_sdmx_ml` read its observation value off an
`itertuples` field named `index`, which silently shadows `tuple.index`; it
worked, and it is exactly the coincidence that stops working.

X-13ARIMA-SEATS is not installed on this machine, so every local run falls
back to STL and is labelled as doing so; the X-13 branch is exercised through
a substituted runner, which is why `_run_x13` is a module-level function with
a narrow contract.

109 new tests (`tests/test_seasonal.py`, `tests/test_outliers.py`,
`tests/test_revision.py`, and the Task 0 additions to
`tests/test_multilateral.py`); 776 in the suite; engine coverage 94%, with
seasonal.py 90%, outliers.py 94%, revision.py 95% and multilateral.py 92%.

Not done: X-11/SEATS quality diagnostics (M and Q statistics, sliding spans)
on the STL path; trading-day, moving-holiday and leap-year adjustment;
seasonal adjustment made additively consistent across an aggregation
structure; a per-category window or method for the multilateral headline;
revision analysis by horizon or decomposed by source; a real-time database of
source data as it arrived; selective editing that ranks outlier flags by
their effect on the aggregate rather than by how many screens agreed.

## Phase 7a - Decomposition, core measures and deflation (done, 2026-09-23)

**Task 0, the three items carried from seasonal adjustment.**
(a) `reporting/exports.SEASONAL_SURFACES` enumerates, in code, every place an
adjusted series reaches a reader: the page, the chart, the method note, the
Markdown and Word reports, the deck, the Excel pack, the bulletin, the CSV and
the SDMX message. `tests/test_seasonal_carried.py` renders each and checks it
names the engine (and says when it is the fallback), carries the unadjusted
series, and states the direct-adjustment warning; a second test scans
`pages/` and `reporting/` and fails if a module handles the adjusted series
without being enumerated. Writing it found one real gap: the SDMX message
carried "seasonally adjusted: All items" with nothing saying what adjusted it.
A `BASIS` series attribute now carries the basis (schema-valid). The page now
draws the shared `charts.seasonal_adjustment_chart` rather than an unlabelled
`st.line_chart`, so the chart on screen names the engine in its title,
legend and note exactly as the exported one does; the chart is also in the
Word report, the deck and the bulletin.
(b) The null test: a pure trend comes back unchanged to 1e-10 (measured
1.7e-14); a noisy season-free series is moved by at most its own noise, sigma,
in root-mean-square terms, and its trend by under 0.1 points a year. The
tolerance is the irregular's standard deviation rather than a fixed
percentage, for the reasons in docs/methodology/seasonal.md; measured over 40
seeds of ten years, a median 0.62 sigma and a worst case of 0.77 sigma. On four
years the worst case reached 1.13 sigma, which is recorded, not hidden.
(c) Every adjustment is direct, and `SeasonalAdjustment.label` now says so and
warns that adjusted components need not sum to an adjusted total. The
constrained adjustment is not implemented; a test on the bundled collection
shows the gap it would close.

**Task 1, decomposition.** `engine/decomposition.py`. Rates: period on
period, year on year, annualised, three months on three (and annualised), and
cumulative. Contributions at every level of the tree, built on
`aggregation.aggregate_tree` and `aggregation.contributions` -- one
definition of the aggregate and one of a contribution -- with each child's
contribution to its parent rescaled to the headline, which nests exactly
because every parent's weighted level is the sum of its children's. The
residual is reported (`residual_pp`), not asserted.

The real-data requirement meant the Phase 2 connector had to learn to label
its rows: `parse_jsonstat` dropped the code of any dimension that varied, so a
request for twelve divisions came back as twelve series' observations in one
unlabelled column. A varying dimension now comes back as a column; a single
series decodes exactly as before. `eurostat.hicp_key` asks the SDMX 2.1
endpoint for several codes by key path (its query-string filters are accepted
and ignored), and `eurostat.hicp_tree` builds the HICP's three-level tree the
way the HICP is compiled within a year. Recorded responses for the euro area
(55 series, 2022-12 to 2025-12, and the 2023-2025 item weights) are the test
fixtures. Re-aggregating the 42 groups reproduces the published all-items
index to within 0.004 points in every month of 2025; contributions reconcile
to eight decimals at every level for every month (residual of order 1e-14).
CP08's lone published group covers 1.20 of its 25.51 per mille, so CP08 is a
leaf and the page says so; CP05's groups sum to 61.01 against 61.02, which the
aggregation reports.

Core measures: exclusion (a parent excludes everything beneath it -- "CP01"
removes food's groups), trimmed mean at a configurable trim with partial
weights at the trim points, weighted median with the even-split convention,
variance weighted on a trailing window with a reported floor, and sticky price
from item-level frequency of change at the Atlanta Fed's 4.3-month cut-off.
All use effective weights, so a zero-trim trimmed mean is the headline
exactly. `core_measure_availability` gives each measure's reason for not
running, and the page prints every measure's requirement beside it: on the
HICP the sticky-price measure is shown as needing item-level prices the
published indices do not carry. Base effects: carry-over plus impulse equals
the year-on-year rate, and this period's movement minus the base effect equals
the change in it, both exact. Diffusion (shares rising, the diffusion index,
the basket share above a threshold) and dispersion (weighted spread and
skew).

**Task 2, deflation.** `engine/deflation.py`. `deflate` with explicit
alignment: frequency read from the data, a mismatch raises naming both, a
missing deflator period raises unless `allow_partial`, and conversion is
`to_frequency`, called by the user and recorded. Real wages and income, exact
real growth beside the "nominal minus inflation" approximation and its error,
constant prices and volume indices, PPP conversion (an annual PPP held through
the year only when asked) and price level indices. Every result's label names
the deflator, the reference period and the alignment.

**The chart rule.** Every artist in `reporting/charts.py` now declares its
unit (`charts.mark`), and `check_figure` refuses an axis that mixes units --
an index level beside a percentage change, a percentage change beside a
percentage-point contribution -- or that puts nominal and real together
without each line and the axis saying which is which. An unmarked artist is
itself a failure. `build_all_charts` checks every figure; the tests check
every chart function and the negative cases. The contributions chart draws its
total as "Total (sum of contributions)" in percentage points rather than as
the headline's percentage change, which is the same number in a different
unit.

**Pages**: Decomposition (the compiled run or the published HICP) and
Deflation, each with page-level AppTests, including the HICP fetched through
the connector and audited, and a run without weights refused with the reason.

**Tests.** 85 new (`test_decomposition.py` 50, `test_deflation.py` 17,
`test_seasonal_carried.py` 18; two in `test_seasonal.py` updated for the
longer label); 862 in
the suite on SQLite; engine coverage 94% (decomposition 94%, deflation 92%).

**Deferred.** Contributions spanning a chain link (the Ribe decomposition for
annually chain-linked indices); a year-on-year contribution across December
uses one set of weights and is labelled an approximation. Chain-linked volume
measures and double deflation. A test for identifiable seasonality before
adjusting. Core measures on seasonally adjusted components. Carrying the
decomposition and deflation results into the report, deck and bulletin: they
are analyst tools with their own downloads, not part of a run's publication,
and wiring them into the release outputs is a publication decision rather than
a methodological one.

## Phase 7b - Spatial, trade, construction and contract escalation (done, 2026-09-23)

**Task 0a, contributions in the release.** `report.contributions_summary`
builds one table -- each category's contribution to the change in All items
on the same period a year earlier, at level 1 of the tree (the run's
categories), ending with the sum, the published change and the residual --
and the bulletin, the Word and Markdown reports and the deck all print it.
The residual is a row, not absorbed into any category; a test replaces the
headline with one the categories do not add up to and checks the residual
row shows it. The Excel pack was left alone (its sheet list is pinned by
`test_exports.py`, and the prompt named the bulletin, Word and deck). A run
without weights gets the reason in place of the table.

**Task 0b, contributions across a chain link.** The prompt's stop condition
was not met: a published treatment exists and was obtained. It is the OECD
note "OECD calculation of contributions to overall annual inflation" (May
2018, updated March 2022), section 3, which follows Walschots (2016,
Statistics Netherlands) and is the formula Balk and Mehrhoff call the "Ribe"
contribution in chapter 8 of Eurostat's HICP Methodological Manual; Eurostat's
own published contributions (`prc_hicp_ctrb`) use it. `ribe_contributions`
implements it on chain-linked indices with each year's weights. Tested on a
constructed case derived by hand, and against Eurostat's published
contributions for every euro-area division and month of 2025 (recorded
fixture): largest gap 0.0056 pp, the rounding of the publication. A single
weight set across the link misses the hand case by over a percentage point,
which is why it is not good enough. On the Decomposition page for the HICP.

**Task 1, spatial.** `engine/spatial.py`: CPD by weighted or unweighted least
squares on region and product dummies, with standard errors; Geary-Khamis by
iteration. Both recover a known solution to 1e-9 with gaps and free
quantities, and a two-region two-product case solved by hand (3 against √8,
the quantity-versus-count difference). The overlap matrix is on every
result; a region whose best pairing shares fewer than `min_overlap` products
(default 5) is withheld from the published parities and from conversion, with
its estimate and the reason shown; a region with no chain to the base is
refused.

**Task 2, trade.** `engine/trade.py`: price indices over products with
`engine/bilateral`'s Fisher, Laspeyres and Paasche; unit value indices that
always carry the bias and the conditions; `unit_value_bias`; terms of trade
that refuse mismatched kinds or bases and reconcile to the ratio exactly.
The demonstration: unchanged prices, a composition shift, a unit value index
of 478.95 against a price index of 100 -- a gap of 378.95 points.

**Task 3, construction.** `engine/construction.py`: input cost index (cost
shares that must match the inputs exactly), output price index (a fixed bill
of quantities at tender rates; a period missing an item is not priced), and
their ratio as the implied margin and productivity movement. The concepts are
stated on every result and first on the page.

**Task 4, escalation.** `engine/escalation.py`: the clause applied in a
stated order (reading, movement, dead band, trigger, indexed share, cap and
collar), a schedule with the payment with and without the limits, and a
summary for a non-specialist that names the index, the vintage, the lag and
every period in which a limit changed the payment. The page leads with the
summary, offers it as a text download, and requires a stated vintage for an
uploaded index; a session compilation is named as not a registered vintage.

**Tests.** New: `test_chain_link_contributions.py` 8,
`test_release_contributions.py` 5, `test_spatial.py` 10, `test_trade.py` 7,
`test_construction.py` 6, `test_escalation.py` 9; 45 in all, 907 in the suite.

**Deferred.** Ribe contributions for the whole tree at once (they are
computed for one level, the components supplied); GEKS/EKS parities,
basic-heading structure and time linking of spatial comparisons; chained or
survey-price trade indices; hedonic or repeat-tender construction output
indices; multi-index escalation formulas and provisional-then-final payments
when a lagged index is revised.

## Phase 8 - Asset and property price indices (done, 2026-09-23)

**Task 0a, contributions in the Excel pack.** A "Contributions" sheet after
"Final index", built from the same `report.contributions_summary` as the
bulletin, the reports and the deck: the note stating the tree level, then
the table ending in the sum, the published change and the residual. The
pinned sheet list (`excel.SHEETS`, asserted in `test_exports.py`) now names
it, and that test asserts it by name.

**Task 0b, spatial on real data.** Succeeded, against Eurostat's PPPs by
analytical category (`prc_ppp_ind`, 2023, recorded). Each country's most
detailed published categories under actual individual consumption are the
products, their PPPs the prices and their national-currency expenditure the
weights. The real data did something constructed data had not: three
countries (JP, US, UK) publish only aggregates and are *disconnected*, and
the engine used to refuse the whole comparison when any region had no chain
to the base. It now withholds such a region with the reason. Of 23 priced
categories only 15 carry expenditure, so the weighted comparison rests on 15;
no connected country fell below the thin-overlap threshold. Against the
published A01 PPPs: median gap 5.5%, explained by coverage (median 49% of
consumption) and systematic (Spearman −0.77 with the price level index).

**Task 1, property price indices.** `engine/asset.py`, five families (six
series, with both repeat sales forms), each stating what it measures and its
limitation. `compare_methods` explains the gaps quantitatively: on the
demonstration market the quality of what sold rose 6.86%, within strata 5.93%
(about 7 of the median's 12.9 points above the hedonic), within cells −1.41%
(about −1.7 against the mix-adjusted mean's −1.3). The first draft of that
explanation asserted the mix was "largely inside" the mix-adjusted mean;
measuring it showed the size bands absorb it, and the text now reports the
measured within-cell shift instead.

**Task 2, rents and owner-occupied housing.** `engine/housing.py`: a matched
rent index, and the four approaches as four questions -- the page shows the
questions side by side and gives each its own section, with no selector.

**Task 3, diagnostics and suppression.** Counts per stratum and period,
coverage by method and of a stock, and stratum suppression through
`suppress_with_secondary`. Repeat sales revisions through
`engine/revision.analyse`, shown on the Revisions page by a display
function the page now shares with registry corrections. Measured on the
demonstration market: mean absolute revision 0.42 index points.

**Task 4, real house price data.** Eurostat publishes indices, not
transactions, so the transaction methods cannot be run on agency data
through an existing connector; the aggregation can be checked, and was: the
published totals rebuilt from the published new- and existing-dwelling
indices and weights, chained at Q4, to within 0.061 index points (DE) and
under 0.02 for IE, NL, FR, ES, DK, PL and the EU.

**Tests.** New: `test_property.py` 22, `test_housing.py` 7,
`test_spatial_eurostat.py` 2, 2 more in `test_release_contributions.py`;
`test_spatial.py`'s disconnected-region test rewritten for the new behaviour; 33 in all, 940 in the suite.

**Deferred.** The arithmetic (value-weighted, IV) Case-Shiller form;
depreciation and renovation adjustment of repeat sales; hedonic double
imputation; land and structure decomposition; quality adjustment of rents;
running the transaction methods on real transactions (HM Land Registry price
paid data would serve, but it is not reachable through an existing
connector).

## Phase 9a - Uncertainty, variance and sensitivity (done, 2026-09-23)

**Task 0, property on real transactions.** Obtained: HM Land Registry price
paid data for 2024 (930,559 records, OGL). What the real data did that
constructed data had not, and what was done, is in `data/price_paid.py` and
docs/methodology/asset.md: no header row and every field quoted (two loader
fixes); 18% category B; prices from GBP 1 to 180 million; duplicate
registrations; no postcode on some; no size, rooms or appraisal; re-sales
within a year dominated by same-day and quick resales; a district-year too
thin for repeat sales (compare_methods now reports an unestimable method
rather than failing); and hedonic diagnostics that did not scale to a dummy
per county (VIF and leverage rewritten, same values). Chosen not to fix:
leasehold houses kept as a characteristic, fixed price bounds, one year only.

**Task 1, sampling variance.** `engine/uncertainty.py`: Rao-Wu bootstrap of
whole clusters within strata, for a declared design; refusal otherwise;
single-cluster strata refused. Coverage 95.5% on a known population over 400
samples (tolerance 92-98%); naive 65.5%, 2.06 times narrower.

**Task 2, sensitivity.** `engine/sensitivity.py` across six dimensions; on
the bundled collection 118.12 (overall-mean imputation) to 138.57 (Carli)
around 135.60. Laspeyres without weights and the run's own aggregation are
excluded as duplicates, and say so; the seasonal treatments are expressed as
a ratio applied to the published headline because they are compiled on their
own aggregator.

**Task 3, where uncertainty appears.** `HEADLINE_SURFACES` (14 pages) and
`HEADLINE_EXEMPT` (3, with reasons); `show_uncertainty` on each; a scan test.

**Tests.** New: `test_price_paid.py` 8, `test_uncertainty.py` 12; 960 in the suite.

**Deferred.** A finite population correction; variance of chained indices
over many links; Taylor linearisation to compare with the bootstrap;
combinations of methodological choices (one is varied at a time); a
multi-year price paid run for repeat sales.

## Phase 9b - Forecasting and scenarios (done, 2026-09-24)

**Task 0, carried items.** The sensitivity spread is labelled a lower bound
wherever it appears (`sensitivity.LOWER_BOUND`: label, chart title, page).
The imputed share of the aggregate sits beside the table
(`sensitivity.imputed_share`, a column per row, and the page's metrics): the
published run imputes nothing; the two settings that move the headline 17
points fill the 10% of Dec 2025's aggregate that is Strawberries out of
season.

**Task 1, forecasting.** `engine/forecasting.py` provides ARIMA, SARIMAX, ETS,
and pass-through and Phillips-curve regressions. Each is backtested from
rolling origins against the random walk or the seasonal naive on the same
window. "Beats" needs a Diebold-Mariano rejection. The model-implied interval
is set against the measured error at every horizon. The regressions are
labelled correlational.

**Task 2, scenarios.** `engine/scenarios.py` applies shocks with stated size,
timing, phase-in and a sourced coefficient to a stated baseline rule, and
draws a fan from the rule's past errors. The assumption list is output.

**Task 3, export discipline.** `engine/projection.missing_parts` and
`reporting/projections.release`; `PROJECTION_EXPORTS`,
`EXPORTS_WITHOUT_PROJECTIONS` and `PROJECTION_MODULES`; the tests scan for
unlisted exports and modules and refuse every export path any missing part.

**Registry.** `ProjectionRunORM` (migration 0008);
`register_projection` / `reproduce_projection` check the backtest and path
digests.

**Deferred.**
- The driver's own uncertainty in a regression's interval (the backtest
  measures what omitting it costs).
- Re-selecting the specification at every backtest origin.
- Forecast combination.
- Second-round effects and shock interactions in scenarios.
- Coefficient uncertainty in the fan.
- A real driver series for the bundled collection. It has none, so the
  regressions were evaluated only on generated data with a known process.

## Release 1.0.0 - audit, close out and package (done, 2026-09-24)

**Wiring audit** repeated (docs/wiring_audit.md), now import-aware: 69
modules, 756 public names (with the follow-up's `core/demo.py`). Seven wired gaps were closed with page-level
tests:
- outlier withdrawal;
- the correction audit event;
- projection reproduction on Audit;
- characteristics replay on Audit;
- Eurostat PPPs on Spatial;
- price level indices on Spatial and Deflation;
- a user-defined classification tree on Ingest.

Dead code was removed.

**Docker** is not installable here without administrator changes. Instead:
- the build context was computed from `.dockerignore`, which led to fixing
  its depth;
- the image's /app was reproduced and the build's ruff, mypy and pytest
  steps run there;
- the editable install was moved after the source copy.
- running the suite in that layout found two faults the repository's runs had
  hidden, both fixed with tests: a test that relied on a git-ignored local
  database, and `run_pipeline` creating an empty SQLite file whenever it ran
  without a database.

The X-13 position is set out for the owner's decision in
docs/release_verification.md.

**Documents**: docs/limitations_register.md, docs/methodology_statement.md.

**Version**: 1.0.0, pyproject and `__version__` reconciled; tag `v1.0.0`,
local.

**Release follow-up** (same version; the unpushed tag was moved to it):
- **Provenance carries the commit.** Runs now record the full commit of
  their own checkout (not wherever the process started), marked `-dirty`
  when the working tree differed from it. Where there is no checkout they
  record the image's build value, or a stated `unknown`.
- **Audit page.** Verification says whether the code reproducing a run is
  the registering commit.
- **Corrected claims.** The methodology statement, the user guide and the
  revision note said reproduction was "byte for byte" / "from exactly that
  record" and that deleting any audit record breaks the chain. Both
  overstated: reproduction uses the code running now, and truncating or
  emptying the log is not detected.
- **Demonstration.** `core/demo.py` seeds one viewer into an empty
  database only, and a banner says what ephemeral storage does to the audit
  log and registry. The administrator guide now says Community Cloud is a
  demonstration host only.
- **X-13 gate.** X-13 runs only with the administrator setting
  `PRICELAB_X13_ENABLED`, and is labelled unvalidated when it does.

**1.0.1**: pushed at the owner's request. CI's first run failed at test
collection: `pypdf` was imported but undeclared. It is now declared (with
`pillow`), and a test fails on any undeclared third-party import. v1.0.0
was published, so it stays where it is; the fix is v1.0.1.

**1.0.2**: 1.0.1's CI failed collection because CI runs a bare `pytest`,
which does not put the repository root on `sys.path`, so `pages` was not
importable; every local run had used `python -m pytest`. The root is now on
pytest's `pythonpath` (tested), and the gate runs the bare form.

## Deferred (not in the agreed scope; revisit if asked)

OIDC (would require hosting beyond
Streamlit Community Cloud); an in-app user-management page; CPA, NACE and HS
classification reference data (no authoritative, machine-readable source
verified yet -- the generic loader that would take one already exists); a
cascading (multi-total) secondary-suppression solver.
