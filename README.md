# PriceLab

**[Live demo →](https://pricequalitylab.streamlit.app/)** (runs the version last pushed,
which predates the 1.0.0 platform build) — upload your own file, or download
[`supermarket_price_collection.xlsx`](supermarket_price_collection.xlsx) from this repo and use that.

Upload a price collection. Get a defensible index, the findings written out, and a
slide deck and report you can send on.

Built for the recurring problem of turning a messy price collection into something
publishable: real collections arrive with sentinel values, unit errors, seasonal
products that leave the shelf, and a sample that rotates underneath you. Handling
those badly is easy and invisible. PriceLab handles them explicitly, shows every
decision it took, and writes the method note that makes the result auditable.

## Run

```bash
make install                                # dependencies, editable install
python scripts/create_user.py \
    --username admin --role administrator   # first account; prompts for a password
make run                                    # local
make compose                                # container, runs lint/type-check/tests during build
```

(`make` targets assume an activated virtual environment and a `make` tool;
on Windows, or for a deployment, follow the step-by-step commands in
[docs/admin_guide.md](docs/admin_guide.md), which were walked end to end on a
clean checkout.) Then open `http://localhost:8501`, sign in, and upload an Excel or CSV file with one
row per item per period on the Ingest page. There is no self-registration: every
account is created with `scripts/create_user.py`, against one of four roles
(`administrator`, `compiler`, `analyst`, `viewer`) — see [Access](#access) below.

A local run creates `pricelab.db` (SQLite) at the repository root the first time it
starts; `make compose` persists the same file in a named Docker volume instead. Set
`PRICELAB_DATABASE_URL` to point either at a different SQLite file or at PostgreSQL
in production — every query goes through SQLAlchemy, so that is the entire migration.

## Access

Four roles, enforced from inside each page's own code, not by hiding a sidebar
entry: `administrator` and `compiler` can use Ingest, Quality, Imputation, Quality
adjustment, Outliers and Index build (compiling a run); `analyst` additionally reaches
Findings, Diagnostics, Multilateral, Seasonality and Revisions
(interpreting one); `viewer` reaches only Reports, which is also where a compiled
run gets registered and, by an administrator, approved. Every login, logout, data
load, configuration change, calculation run, quality or imputation override, and
export is written to an append-only, hash-chained audit log in the database —
`core.audit.verify_chain()` detects a tampered or deleted record and names where the
chain breaks.

An analyst or viewer with no run of their own to look at can load a previously
*approved* run instead: `core.registry.reproduce()` re-executes it from its stored
input data and configuration, so what they see is provably the same figure that was
signed off, not a re-upload of a possibly different file.

## What it does, in order

1. **Infers the schema.** Maps your columns by name, then resolves ambiguity by
   cardinality, because the category column always has fewer distinct values than
   the item column even when both are called something unhelpful.
2. **Validates structure** and reports every fault in one pass rather than stopping
   at the first. The key uniqueness check is what licenses a matched comparison
   downstream: with duplicate keys the index is undefined.
3. **Diagnoses missingness by mechanism.** Gaps that recur in the same calendar
   months across years are seasonal unavailability. Gaps that hit every item at once
   without recurring are collection failure. They get different treatments, chosen
   automatically and shown to you.
4. **Detects and repairs unit errors.** Each observation is compared against a
   centred rolling median of its own item's series. Faults that sit a clean order of
   one hundred from that level are rescaled, not deleted, because deletion breaks
   the item continuity a matched index depends on. A residual check afterwards is
   the evidence the rule was correctly specified.
5. **Builds a matched-model index.** Jevons by default, chained, with the formula,
   chaining and imputation all overridable. Uploads carrying quantities (or
   expenditure) unlock Laspeyres, Paasche, Fisher, Törnqvist, Walsh,
   Marshall-Edgeworth, the geometric forms and the unit value.
6. **Writes the findings.** Ranks what it found by materiality, states each with the
   number behind it, and names an action.
7. **Exports.** A slide deck with speaker notes, a Word report with the full method
   note, the cleaned data with every alteration flagged, and a configuration file
   that reproduces the lot.

## Design decisions

**All logic sits in the library.** `app.py` contains no index arithmetic. This is
what makes the methods testable, and it means the same code runs headless in a batch
pipeline without an interface.

**The tool decides, then shows its working.** Automatic configuration is what makes
it usable, but a tool that makes choices you cannot see is one you cannot defend.
Every automatic decision appears in the interface with the reason, and every one is
overridable.

**It does not fit its rules to the data.** Gap treatment is inferred, because the
mechanism is a fact about the collection. The outlier band and the index formula are
not, because a rule fitted to the faults it is meant to detect will always find them,
and a formula chosen to suit a dataset is a result rather than a method.

**Nothing is modified silently.** Every altered observation carries a flag naming
what was done and why, and the flagged set is exportable. If a threshold is disputed,
change it and re-run; you are never defending a dataset you can no longer decompose.

**No finding claims a cause.** The data shows that replacement items arrive above the
category level. It does not show why. The wording holds that line throughout.

**Figures are built outside pyplot's global registry**, because `plt.subplots` keeps
every figure alive in a module-level list and that is a slow memory leak in a
long-running server.

## Tests

```bash
make test        # 1032 tests
make lint        # ruff
make typecheck   # mypy strict, scoped to core/, engine/, data/ and reporting/
```

The index tests assert axiomatic properties rather than fixed expected numbers. A
test asserting an index equals 147.3 tells you nothing when it breaks; a test
asserting that Jevons satisfies time reversal, that Carli does not, and that Dutot is
sensitive to the quantity unit while Jevons is not, encodes the reason each formula
was chosen or rejected.

`tests/test_axioms.py` generates that data with Hypothesis rather than fixing it, so
the tests look for a counterexample instead of confirming the case the author thought
of: identity, proportionality, commensurability, time reversal, factor reversal, the
Laspeyres/Fisher/Paasche ordering under substitution, the arithmetic/geometric/harmonic
bias ranking, and exact additivity of contributions on random four-level trees. The
failures are asserted too — Carli must fail time reversal and Dutot must fail
commensurability — because a suite testing only the passing cases would go green if
every formula were silently replaced by Jevons.

`tests/test_golden_values.py` reproduces the published worked example in the CPI
Manual 2020 (Chapter 8, Tables 8.1–8.3) number for number, at the manual's own
published precision. `tests/test_quality_adjustment.py` does the same for Chapter 6's
worked examples of missing prices and quality change (equation 6.4, the targeted-mean
chain, Table 6.4a, Table 6.5 and the option-cost example), and
`tests/test_hedonic.py` recovers a known quality effect from a synthetic panel whose
true characteristics prices are known by construction.

Covered: identity, proportionality, time reversal, unit invariance, matching
behaviour, recovery of a known growth rate, level holding when nothing matches, scale
error repair in both directions, the requirement that genuine volatility is *not*
flagged, mechanism classification for seasonal and collection gaps, class mean
imputation moving a gap by its peers' change, structural validation failures,
configuration round tripping, schema inference from unconventional column names,
automatic treatment selection, deck and report validity, speaker notes present, the
method note stating its limitations, and a clean file producing a clean report rather
than invented problems. A later pass added edge cases a normal run never exercises:
a single-period upload, a single-category upload, gap seasonality with under two years
of history, a single-item category with no peer to impute from, a misconfigured
base period, and a zero base price in the sensitivity diagnostics — each added after
being found to silently produce a wrong number rather than an honest one.

## Test dataset

`scripts/generate_synthetic_data.py` writes `supermarket_price_collection.xlsx`
to the project root (sheets `Price_Data` and `Data_Dictionary`; columns `Date`,
`Category_Num`, `Category`, `Item_ID`, `Item_Name`, `Reported_Price`): a
monthly supermarket collection, January 2015 to December 2025, 10 categories,
87 items, about 6,600 rows. It is built, deliberately, with the faults this
tool exists to handle, so that running PriceLab against it is a real exercise
of every module rather than a demonstration on tidy data:

- Sentinel zeros from three distinct mechanisms: Strawberries goes off-shelf
  in the same seven calendar months every year (seasonal), Pasta is unpriced
  across every item for three consecutive months in spring 2020 (collection
  failure), and every category carries scattered single-item gaps (sporadic).
- Unit errors at exactly 100x and 0.01x the item's own level, away from the
  edges of each item's run.
- Item churn: each category is a fixed number of concurrent shelf slots, each
  refilled by a new item, at a price nudged above or below the category rate,
  whenever the incumbent's tenure ends.
- A different trend and seasonal amplitude per category, including DVDs in
  sustained deflation and Ice cream and Sun cream on strong summer peaks.

Nothing in `pricelab` reads this script or is tuned to what it produces; the
generator exists to give the pipeline something honest to be tested against.
Regenerate it with:

```bash
python scripts/generate_synthetic_data.py
```

The `example_analysis.pptx` and `example_report.docx` at the project root
were produced by uploading this file through the running app with no
configuration changes.

## Structure

```
pricelab/
  core/
    config.py         RunConfig (pydantic) and Settings, the units of reproducibility
    models.py         pydantic domain schemas and the DataFrame column-contract check
    security.py       RBAC, authentication, export sanitisation, the restricted
                       formula evaluator, small-cell suppression
    db.py             SQLAlchemy engine, session factory, declarative base
    audit.py          append-only, hash-chained audit log
    registry.py       index run registry: register, approve, correct, reproduce
    cache.py          bounded, content-hash-keyed analysis result cache
  data/
    upload.py          column mapping and structural validation (legacy path)
    loaders.py         upload pipeline: format dispatch, encoding/delimiter
                       detection, header inference, memory-footprint check
    mapping.py         confidence-scored column mapping, confirmed and
                       stored per file hash
    validation.py      completeness/validity/consistency/uniqueness/
                       timeliness/coverage/conformity/plausibility findings,
                       with a persisted override workflow for critical ones
    classification.py  COICOP 2018 full tree, plus a generic loader for a
                       user-defined or other classification's own tree
    store.py           immutable raw layer, cleaned layer, replayable
                       transformation log
    connectors/        ONS, Eurostat, IMF, World Bank, OECD, BLS, FAO and
                       a generic SDMX 2.1 connector
  engine/
    quality.py         sentinel recoding, mechanism classification, fault repair
    imputation.py       none | carry_forward | class_mean | seasonal_hold |
                        targeted_mean | overall_mean, with response rates
    index.py            jevons | dutot | carli | custom, and with quantities
                        laspeyres | paasche | fisher | tornqvist | walsh |
                        marshall_edgeworth | geometric_* | unit_value;
                        matched, chained or fixed
    elementary.py       the elementary aggregates, each returning its sample
                        size and imputation count alongside the value
    bilateral.py        laspeyres | paasche | fisher | tornqvist | walsh |
                        marshall_edgeworth | lowe | young | geometric variants
    aggregation.py      weighted roll-up through the classification tree, with
                        exactly additive contributions
    splicing.py         rebasing, link factors, chaining, chain drift
    multilateral.py     geks_fisher | geks_tornqvist | tpd | wtpd | tdh |
                        geary_khamis over a window, with window extension by
                        movement | window | half | mean splice, FBEW and FBMW,
                        the spread across every combination, the roll-up to an
                        all-items headline, and the year-over-year and
                        rolling-year seasonal forms
    seasonal.py         strictly seasonal items; class confinement and weight
                        update, with the gap between them reported; the
                        Rothwell index; counter-seasonal estimation; seasonal
                        adjustment by X-13ARIMA-SEATS or STL, with whichever
                        actually ran named in every output
    outliers.py         tukey | quartile | hidiroglou_berthelot | ratio screens
                        over price relatives, feeding a review queue; no code
                        path excludes a quote without an analyst and a reason
    revision.py         revision triangles over the registry's own vintages,
                        mean and mean absolute revision, a bias test, and
                        published against current for any reference period
    decomposition.py    rates of change; contributions at every level of the
                        tree, reconciled to eight decimal places; exclusion,
                        trimmed mean, weighted median, variance-weighted and
                        sticky-price core measures, each stating its
                        parameters and data requirements; base effects;
                        diffusion and dispersion
    deflation.py        deflation with explicit frequency alignment (a
                        mismatch raises, never resamples); real wages and
                        income; constant prices and volume indices; PPPs
    spatial.py          country product dummy and Geary-Khamis parities, the
                        matched products for every region pair, thin overlap
                        reported and withheld, conversion at the parities
    trade.py            import and export price indices, unit value indices
                        with their bias stated on every result, terms of trade
    construction.py     construction input cost and output price indices, with
                        the difference between them stated on every result
    escalation.py       contract indexation (lag, averaging, dead band, trigger,
                        indexed share, cap, collar) with a payment schedule and
                        a plain-language summary of the clause as applied
    asset.py            residential property price indices: stratified median,
                        mix-adjusted mean, repeat sales (BMN and Case-Shiller),
                        SPAR and hedonic, each stating what it measures and
                        rests on; the differences explained; repeat sales
                        revisions through engine/revision.py
    housing.py          rental price index; owner-occupied housing as four
                        questions (rental equivalence, net acquisitions, user
                        cost, payments)
    uncertainty.py      design-based bootstrap intervals for index movements
                        (whole clusters within strata), refused when no design
                        is declared
    sensitivity.py      the headline under every defensible alternative choice;
                        a range, labelled and drawn apart from any interval, a
                        lower bound, with the imputed share of the aggregate
    projection.py       what every forecast and scenario carries: interval,
                        backtest, benchmark comparison, stated assumptions
    forecasting.py      ARIMA, SARIMAX, ETS, pass-through and Phillips-curve
                        regressions, backtested from rolling origins against a
                        random walk or the seasonal naive
    scenarios.py        shocks to energy, the exchange rate, wages and
                        administered prices on a stated baseline, with a fan
                        and the assumption list as part of the output
    custom.py           analyst-defined formulae via the restricted evaluator
    quality_adjustment.py
                        overlap, direct comparison, quantity, option cost,
                        class/targeted/overall mean imputation; the ledger's
                        application to a panel; the impact report
    hedonic.py          time-dummy, characteristics-price and imputation
                        hedonics in log-linear, semi-log and Box-Cox forms,
                        with the diagnostics reported, not buried
    diagnostics.py      formula sensitivity, chain drift, unmatched comparison,
                        seasonality, churn
    auto.py             schema inference and self-configuration from the diagnosis
    insights.py         the interpretation layer: ranked findings in plain English
  reporting/
    charts.py           one chart factory serving both the screen and the exports;
                        every artist declares its unit, and a figure mixing
                        units (or unlabelled nominal and real) on one axis
                        is refused
    deck.py             automatic slide deck, structured by what was actually found
    report.py           Word and Markdown report, plus the method note
    excel.py            the Excel evidence pack, nine sheets, sanitised
    bulletin.py         the PDF statistical bulletin, headline from the registry
    exports.py          the publication table with disclosure control; stamped
                        CSV; SDMX-ML 2.1 validated against the standard's XSDs
    projections.py      the only way a forecast or scenario leaves: refused
                        without its interval, backtest, benchmark and
                        assumptions; the export inventory
    readback.py         reads the provenance stamp back out of every format
pages/            one module per lifecycle stage; app.py wires them into
                  role-filtered st.navigation
  ingest.py, quality.py, outliers.py, imputation.py, quality_adjustment.py,
  index_build.py, multilateral.py, seasonal.py, decomposition.py,
  deflation.py, spatial.py, trade.py, construction.py, escalation.py,
  property.py, housing.py, uncertainty.py, forecasting.py, scenarios.py,
  findings.py, diagnostics.py,
  reports.py, sources.py, revisions.py, audit_log.py,
  common.py (shared session-state helpers)
migrations/       Alembic; eight revisions covering users, sessions, audit events,
                  index runs (with the registered headline and data vintage),
                  the classification tree, validation overrides, column
                  mappings, the quality adjustment ledger, the outlier
                  review queue's decisions and registered projections
tests/            1032 tests
scripts/
  generate_synthetic_data.py   the price-quote fixture, not the product
  generate_scanner_data.py     the scanner transaction fixture, with churn,
                               promotions and a quality gradient
  create_user.py               bootstraps a login (no self-registration)
  backup.py, restore.py        the database and the Parquet store, with a manifest
app.py            auth gate, shared chrome, page registry; no analytical logic
Dockerfile        lint, type-check and tests all run during the build
requirements.lock every dependency pinned, compiled by uv from pyproject.toml;
                  local installs, CI and the image all install from it
requirements.txt  generated from the lock, runtime only: what Streamlit
                  Community Cloud installs (never edit by hand; make requirements)
docker-compose.yml the container, PostgreSQL, a named volume for the store,
                  and a `tests` profile that runs the suite against PostgreSQL
docs/backlog.md   the phased plan for growing this into a governed,
                  multi-domain index platform
docs/methodology/ one note per engine module: formula, citation, assumptions, biases
docs/methodology_statement.md  how an index is compiled, for a statistician
docs/limitations_register.md   every known limitation and what it means
docs/wiring_audit.md           every module, reachable from a page or why not
docs/release_verification.md   what 1.0.0 verified, what it could not, the X-13 decision
docs/user_guide.md, docs/admin_guide.md, docs/tutorial.md, CHANGELOG.md
```

The package is organised into four subpackages so it can grow without losing
the original discipline: `engine/` is pure calculation with no Streamlit
import, `data/` is everything about getting a collection in, `core/` is
cross-cutting governance (configuration, security, persistence, the audit
log and the run registry), and `reporting/` is everything a finished
analysis turns into for someone else to read. `pricelab/__init__.py`
re-exports the public surface, so nothing outside the package needs to know
which subpackage a function actually lives in.

`app.py` itself holds no page content any more: it is the authentication
gate, the shared sidebar chrome, and the `st.navigation` registry that wires
`pages/*.py` together. Each page's `render()` is wrapped in
`core.security.require_role`, checked from inside the function body on
every call, so a page refuses an unpermitted role exactly the same way
whether it was reached through the sidebar or called directly — the sidebar
menu itself only *hides* an entry a role cannot use, as a convenience on
top of that, never as the actual control.

## Known limitations

Every known limitation, where it arose, why it was left and what it means for
someone relying on an output, is in
[docs/limitations_register.md](docs/limitations_register.md), grouped by
whether it touches a published number, only an analyst view, or only
operations. How the platform compiles an index, and what has been verified
against published figures, is in
[docs/methodology_statement.md](docs/methodology_statement.md). The three a
reader most often needs first:
- a replacement's price gap is attributed to quality unless a compiler values
  it (the matched-model default);
- a seasonally adjusted series from this release is STL's, because
  X-13ARIMA-SEATS has never run against the real program here;
- no sampling interval exists without a declared design.

The Docker image and compose stack have not been built on this release (no
container engine on the development machine); what was checked without one is
in [docs/release_verification.md](docs/release_verification.md).
