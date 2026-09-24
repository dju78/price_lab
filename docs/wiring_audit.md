# Wiring audit (release)

The Phase 10.5 audit repeated after Phases 5, 6, 7a, 7b, 8, 9a and 9b. Every
module in `core/`, `engine/`, `data/` (with `data/connectors/`) and
`reporting/`: 69 modules, 760 public names, of which
726 are reachable from a page, `app.py` or the package's own
`analyse`/`run_pipeline`.

**Method.** A static, import-aware reachability pass (`ast`). The roots are
every page module, `app.py` and `pricelab/__init__.py`. A public function,
class or constant is reached when its name is referenced from a root, or
from the body of a reached definition, *in a file that imports its module*
(or is its module), to a fixed point. Every name left unreached was then
checked by hand, and the table says what it is. The call path is the
shortest found, read from the definition back to the page (`<-`).

**Classes.** *Reachable*: called from a page or report builder. *Operational
entry point*: run by a script, the container or the migrations, not a page.
*Test hook*: resets state between tests. *Library*: a tested building block
nothing in the product needs, awaiting nothing. *Wired gap*: a capability the
product was meant to offer whose code existed but that no page called (the
`store.py` kind); all are now closed. *Dead code*: removed.

## Gaps found and closed

| Gap | Where it was | Now | Test |
|---|---|---|---|
| Withdrawing an outlier decision | `ledger.withdraw_outlier_decision` had no caller; decisions could be superseded but not withdrawn, and `OUTLIER_DECISION_WITHDRAWN` was never logged | Outliers page, *Withdraw a decision*, reason required, audited | `test_outliers.py::test_the_page_withdraws_a_decision_with_a_reason_and_logs_it` |
| The correction audit event | `RUN_CORRECTED` was never recorded; a correction was logged as an ordinary calculation | Reports page records `run_corrected` | `test_wiring.py::test_reports_page_registers_a_correction_of_an_approved_run` |
| Reproducing a registered forecast or scenario | `registry.reproduce_projection` and `scenarios.scenario_from_spec` reachable only from tests | Audit page, *Verify a registered forecast or scenario* | `test_release_wiring.py::test_the_audit_page_rebuilds_a_registered_projection_and_checks_it` |
| Replaying the characteristics layers | `store.replay_characteristics`: the layers were written (Quality adjustment) but never read back -- the `store.py` pattern again | Audit page, *Replay the characteristics layers* | `test_release_wiring.py::test_the_audit_page_replays_the_characteristics_layers` |
| Eurostat's published PPPs | `eurostat.ppp_comparison_inputs`, the Phase 8 real-data check, ran only in a test | Spatial page, *Eurostat's published PPPs by category*, through the connector, beside Eurostat's own aggregate parity | `test_release_wiring.py::test_the_spatial_page_compares_eurostat_s_published_ppps_and_gives_price_levels` |
| Price level indices | `spatial.price_level_indices` and `deflation.price_level_index` had no caller, though the methodology notes list them | Spatial and Deflation pages, from an exchange-rate upload | the spatial test above; `test_release_wiring.py::test_the_deflation_page_gives_a_price_level_index_beside_a_ppp_conversion` |
| A user's own classification tree | `classification.load_user_defined_tree` had no caller: only COICOP could be rolled up through | Ingest page, *Your own classification tree*; the pipeline then rolls a coded collection up through it | `test_release_wiring.py::test_ingest_loads_a_user_defined_tree_that_the_pipeline_rolls_up_through` |

## Dead code removed

`engine/index.FORMULAE` (a dispatch table nothing read);
`engine/seasonal.TREATMENTS`, `ENGINES`, `treatment_table`,
`available_treatments`; `reporting/readback.is_office_zip`;
`engine/projection.PARTS` and the `Projection` protocol;
`data/classification.COICOP_2018_DIVISIONS` and `seed_coicop_divisions`
(Phase 1's hand-typed divisions, superseded by the full COICOP 2018 tree in
Phase 1's patch; their one test now seeds the full tree).

## Did the surface inventories help?

Partly. `HEADLINE_SURFACES`/`HEADLINE_EXEMPT`, `exports.SEASONAL_SURFACES`
and the projection lists answer a different question from this audit: they
say that wherever a published number appears it carries its label,
uncertainty or four parts. They made the *output* side of the audit quick --
every page that shows a headline or a projection was already listed with
what it shows, so none needed tracing by hand -- and the projection module
list did catch something on the way: wiring projection reproduction into
the Audit page failed its scan until the page was added to
`PROJECTION_MODULES`. But none of the seven gaps was on an output surface;
all were capabilities with no caller, which only a reachability pass finds.
The inventories did not replace the pass; they shortened the part after it.

## Every module

| Module | Reached / public | Status | Call path (first found) | Not reached from a page, and why |
|---|---|---|---|---|
| `core/audit` | 42/42 | reachable | core/audit.GENESIS_HASH ← pages/audit_log.py | every public name |
| `core/backup` | 0/4 | not from a page | — | `BackupError` — operational: raised by the backup scripts; `backup` — operational entry point: `scripts/backup.py`; `verify` — operational: `restore` (scripts/restore.py) verifies the backup manifest before restoring; `restore` — operational entry point: `scripts/restore.py` |
| `core/cache` | 4/5 | reachable in part | core/cache.content_key ← pages/common.py | `reset_analysis_cache` — test hook |
| `core/config` | 15/15 | reachable | core/config.Schema ← pages/ingest.py | every public name |
| `core/db` | 5/6 | reachable in part | core/db.init_db ← app.py | `reset_db_state` — test hook |
| `core/demo` | 10/10 | reachable | core/demo.DEMO_BANNER ← app.py | every public name |
| `core/health` | 0/4 | not from a page | — | `liveness` — operational entry point: `python -m pricelab.core.health`, the Dockerfile HEALTHCHECK and the compose healthchecks; `readiness` — as `liveness`; `serve` — as `liveness` (`--serve PORT`); `main` — as `liveness` |
| `core/ledger` | 10/10 | reachable | core/ledger.record_adjustment ← pages/quality_adjustment.py | every public name |
| `core/logging` | 5/5 | reachable | core/logging.configure_logging ← app.py | every public name |
| `core/models` | 9/9 | reachable | core/models.Role ← pages/construction.py | every public name |
| `core/provenance` | 4/4 | reachable | core/provenance.build_stamp ← pages/forecasting.py | every public name |
| `core/ratelimit` | 3/4 | reachable in part | core/ratelimit.RateLimited ← pages/ingest.py | `reset_upload_limiter` — test hook |
| `core/registry` | 17/17 | reachable | core/registry.DIRTY_SUFFIX ← pages/audit_log.py | every public name |
| `core/security` | 23/24 | reachable in part | core/security.AccessDenied ← app.py | `AuthProvider` — the interface `PasswordAuthProvider` implements |
| `engine/aggregation` | 7/7 | reachable | engine/aggregation.AggregationResult ← pages/index_build.py | every public name |
| `engine/asset` | 19/21 | reachable in part | engine/asset.PropertyError ← pages/property.py | `METHODS` — library metadata (the property methods' names), read by the tests; `true_index` — library: the known-population generator the property tests measure the methods against |
| `engine/auto` | 3/3 | reachable | engine/auto.infer_schema ← pricelab/__init__.py | every public name |
| `engine/bilateral` | 16/19 | reachable in part | engine/bilateral.price_updating_from_panel ← pages/index_build.py | `fisher_quantity` — library: quantity index, used by the factor-reversal axiom tests; the platform publishes price indices; `value_ratio` — library: as `fisher_quantity`; `BILATERAL_REQUIREMENTS` — library metadata, tested |
| `engine/construction` | 6/6 | reachable | engine/construction.CONCEPTS ← pages/construction.py | every public name |
| `engine/custom` | 11/12 | reachable in part | engine/custom.ELEMENTARY_VARIABLES ← pages/ingest.py | `evaluate_elementary` — library: the reference implementation the tests check the pipeline's vectorised custom formula (`index._custom_np`) against |
| `engine/decomposition` | 30/30 | reachable | engine/decomposition.RECONCILIATION_DECIMALS ← pages/decomposition.py | every public name |
| `engine/deflation` | 12/15 | reachable in part | engine/deflation.FREQUENCY_NAMES ← pages/deflation.py | `real_wage` — library convenience; the Deflation page reaches the same computation through `deflate(kind="real wage")`; `real_income` — as `real_wage` (`kind="real income"`); `constant_prices` — as `real_wage` (`kind="constant price value"`) |
| `engine/diagnostics` | 5/5 | reachable | engine/diagnostics.unmatched_comparison ← reporting/charts.build_all_charts ← pricelab/__init__.py | every public name |
| `engine/elementary` | 9/9 | reachable | engine/elementary.ELEMENTARY_FORMULAE ← engine/custom.ELEMENTARY_VARIABLES ← pages/ingest.py | every public name |
| `engine/escalation` | 4/4 | reachable | engine/escalation.EscalationError ← pages/escalation.py | every public name |
| `engine/forecasting` | 11/11 | reachable | engine/forecasting.METHODS ← pages/forecasting.py | every public name |
| `engine/hedonic` | 12/13 | reachable in part | engine/hedonic.FUNCTIONAL_FORMS ← pages/quality_adjustment.py | `synthetic_hedonic_panel` — library: demonstration and test data generator |
| `engine/housing` | 9/9 | reachable | engine/housing.OOH_QUESTIONS ← pages/housing.py | every public name |
| `engine/imputation` | 10/10 | reachable | engine/imputation.response_rates ← pages/imputation.py | every public name |
| `engine/index` | 15/15 | reachable | engine/index.jevons ← pricelab/__init__.py | every public name |
| `engine/insights` | 9/9 | reachable | engine/insights.Finding ← pricelab/__init__.py | every public name |
| `engine/multilateral` | 28/28 | reachable | engine/multilateral.METHODS ← pages/seasonal.py | every public name |
| `engine/outliers` | 19/19 | reachable | engine/outliers.METHODS ← pages/outliers.py | every public name |
| `engine/projection` | 8/8 | reachable | engine/projection.ProjectionIncomplete ← pages/forecasting.py | every public name |
| `engine/quality` | 11/11 | reachable | engine/quality.run_quality ← pricelab/__init__.py | every public name |
| `engine/quality_adjustment` | 21/21 | reachable | engine/quality_adjustment.QualityAdjustmentError ← pages/quality_adjustment.py | every public name |
| `engine/revision` | 11/11 | reachable | engine/revision.RevisionError ← pages/revisions.py | every public name |
| `engine/scenarios` | 10/10 | reachable | engine/scenarios.DRIVERS ← pages/scenarios.py | every public name |
| `engine/seasonal` | 26/26 | reachable | engine/seasonal.X13_VALIDATED ← pages/seasonal.py | every public name |
| `engine/sensitivity` | 6/6 | reachable | engine/sensitivity.LOWER_BOUND ← pages/uncertainty.py | every public name |
| `engine/spatial` | 9/9 | reachable | engine/spatial.METHODS ← pages/spatial.py | every public name |
| `engine/splicing` | 7/10 | reachable in part | engine/splicing.ChainDriftReport ← engine/multilateral.drift_against_chained ← pages/multilateral.py | `rebase_to_config` — library, tested; the pipeline rebases inside `engine/index`; `chain` — library, tested; the pipeline chains inside `engine/index`; `price_update` — library, tested; the per-item form `bilateral.price_update_shares` is wired (Index build, price-updating report) |
| `engine/trade` | 11/11 | reachable | engine/trade.FLOWS ← pages/trade.py | every public name |
| `engine/uncertainty` | 9/9 | reachable | engine/uncertainty.NOT_QUANTIFIED ← pages/common.py | every public name |
| `data/classification` | 5/9 | reachable in part | data/classification.ClassificationNodeORM ← pages/ingest.py | `REFERENCE_DIR` — reached from `migrations/versions/0003_full_coicop_tree.py`; `COICOP_2018_CSV` — as `REFERENCE_DIR`; `load_classification_from_csv` — as `seed_coicop_2018`, which calls it; `seed_coicop_2018` — as `REFERENCE_DIR`: the migration seeds the COICOP 2018 tree |
| `data/connectors/_jsonstat` | 2/2 | reachable | data/connectors/_jsonstat.validate_jsonstat ← data/connectors/generic_sdmx.GenericSDMXConnector ← pages/sources.py | every public name |
| `data/connectors/base` | 9/9 | reachable | data/connectors/base.ConnectorError ← pages/decomposition.py | every public name |
| `data/connectors/bls` | 2/2 | reachable | data/connectors/bls.BLSConnector ← pages/sources.py | every public name |
| `data/connectors/eurostat` | 16/16 | reachable | data/connectors/eurostat.EurostatConnector ← pages/decomposition.py | every public name |
| `data/connectors/fao` | 2/2 | reachable | data/connectors/fao.FAOConnector ← pages/sources.py | every public name |
| `data/connectors/generic_sdmx` | 1/1 | reachable | data/connectors/generic_sdmx.GenericSDMXConnector ← pages/sources.py | every public name |
| `data/connectors/imf` | 2/2 | reachable | data/connectors/imf.IMFConnector ← pages/sources.py | every public name |
| `data/connectors/oecd` | 2/2 | reachable | data/connectors/oecd.OECDConnector ← pages/sources.py | every public name |
| `data/connectors/ons` | 3/3 | reachable | data/connectors/ons.ONSConnector ← pages/sources.py | every public name |
| `data/connectors/worldbank` | 2/2 | reachable | data/connectors/worldbank.WorldBankConnector ← pages/sources.py | every public name |
| `data/loaders` | 14/14 | reachable | data/loaders.LoaderError ← pages/ingest.py | every public name |
| `data/mapping` | 7/7 | reachable | data/mapping.MappingSuggestion ← pages/ingest.py | every public name |
| `data/price_paid` | 5/5 | reachable | data/price_paid.parse_price_paid ← pages/property.py | every public name |
| `data/store` | 14/14 | reachable | data/store.ImmutableLayerError ← pages/audit_log.py | every public name |
| `data/upload` | 4/4 | reachable | data/upload.ValidationReport ← pricelab/__init__.py | every public name |
| `data/validation` | 8/8 | reachable | data/validation.Severity ← pages/ingest.py | every public name |
| `reporting/bulletin` | 6/6 | reachable | reporting/bulletin.BulletinError ← pages/reports.py | every public name |
| `reporting/charts` | 35/35 | reachable | reporting/charts.to_png ← pricelab/__init__.py | every public name |
| `reporting/deck` | 23/23 | reachable | reporting/deck.build_deck ← pricelab/__init__.py | every public name |
| `reporting/excel` | 2/2 | reachable | reporting/excel.build_evidence_pack ← pages/reports.py | every public name |
| `reporting/exports` | 16/18 | reachable in part | reporting/exports.stamped_csv ← pages/reports.py | `SEASONAL_SURFACES` — surface inventory, read by its scan test; `validate_sdmx_ml` — library: validation against the SDMX XSDs vendored under tests/fixtures (used by the export tests) |
| `reporting/projections` | 7/10 | reachable in part | reporting/projections.path_table ← pages/scenarios.py | `PROJECTION_EXPORTS` — surface inventory, read by its scan test; `EXPORTS_WITHOUT_PROJECTIONS` — surface inventory, read by its scan test; `PROJECTION_MODULES` — surface inventory, read by its scan test |
| `reporting/readback` | 8/8 | reachable | reporting/readback.StampNotFound ← pages/audit_log.py | every public name |
| `reporting/report` | 20/20 | reachable | reporting/report.method_note ← pricelab/__init__.py | every public name |
