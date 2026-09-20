# PriceLab

**[Live demo →](https://pricequalitylab.streamlit.app/)** — upload your own file, or download
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

Then open `http://localhost:8501`, sign in, and upload an Excel or CSV file with one
row per item per period on the Ingest page. There is no self-registration: every
account is created with `scripts/create_user.py`, against one of four roles
(`administrator`, `compiler`, `analyst`, `viewer`) — see [Access](#access) below.

A local run creates `pricelab.db` (SQLite) at the repository root the first time it
starts; `make compose` persists the same file in a named Docker volume instead. Set
`PRICELAB_DATABASE_URL` to point either at a different SQLite file or at PostgreSQL
in production — every query goes through SQLAlchemy, so that is the entire migration.

## Access

Four roles, enforced from inside each page's own code, not by hiding a sidebar
entry: `administrator` and `compiler` can use Ingest, Quality, Imputation and Index
build (compiling a run); `analyst` additionally reaches Findings and Diagnostics
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
   chaining and imputation all overridable.
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
make test        # 126 tests
make lint        # ruff
make typecheck   # mypy strict, scoped to core/ and engine/
```

The index tests assert axiomatic properties rather than fixed expected numbers. A
test asserting an index equals 147.3 tells you nothing when it breaks; a test
asserting that Jevons satisfies time reversal, that Carli does not, and that Dutot is
sensitive to the quantity unit while Jevons is not, encodes the reason each formula
was chosen or rejected.

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
    upload.py          column mapping and structural validation
    classification.py  COICOP 2018 divisions, seeded reference data
  engine/
    quality.py         sentinel recoding, mechanism classification, fault repair
    imputation.py       none | carry_forward | class_mean | seasonal_hold
    index.py            jevons | dutot | carli | laspeyres, matched, chained or fixed
    diagnostics.py      formula sensitivity, chain drift, unmatched comparison,
                        seasonality, churn
    auto.py             schema inference and self-configuration from the diagnosis
    insights.py         the interpretation layer: ranked findings in plain English
  reporting/
    charts.py           one chart factory serving both the screen and the exports
    deck.py             automatic slide deck, structured by what was actually found
    report.py           Word and Markdown report, plus the method note
pages/            one module per lifecycle stage; app.py wires them into
                  role-filtered st.navigation
  ingest.py, quality.py, imputation.py, index_build.py, findings.py,
  diagnostics.py, reports.py, common.py (shared session-state helpers)
migrations/       Alembic; one revision covering users, sessions, audit events,
                  index runs and the classification tree
tests/            126 tests
scripts/
  generate_synthetic_data.py   the test fixture, not the product
  create_user.py               bootstraps a login (no self-registration)
app.py            auth gate, shared chrome, page registry; no analytical logic
Dockerfile        lint, type-check and tests all run during the build
docker-compose.yml the container plus a named volume for the SQLite file
docs/backlog.md   the phased plan for growing this into a governed,
                  multi-domain index platform
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

No quality adjustment between a departing item and its replacement, so a change in
specification is implicitly treated as price change. No expenditure weights unless a
weight column is supplied, in which case the aggregate is equally weighted and
indicative. Seasonal treatment is limited to holding the level across an out-of-season
gap; a counter-seasonal fixed weight approach is not implemented. Mechanism
classification is a proposal for a human to confirm, not a determination.

Only the thirteen top-level COICOP 2018 divisions are seeded as classification
reference data; deeper groups and classes are not, rather than transcribed
without a verified source (see `data/classification.py`). Small-cell secondary
suppression (`core.security.suppress_with_secondary`) handles one published total
per group; a hierarchy with several overlapping totals over the same cells would
need a cascading solver this does not implement. User accounts are provisioned with
`scripts/create_user.py`; there is no in-app user-management page yet. Multilateral
methods, hedonic quality adjustment, deflation, spatial comparison, asset and trade
indices, forecasting and PostgreSQL/OIDC are all out of scope for the current phase
— see `docs/backlog.md` for what is planned and what is deliberately deferred.

The Docker image pins `python:3.12-slim`, the version the test suite and the
interface were verified against. A very new CPython (3.14, at the time of
writing) paired with Streamlit's file-watcher thread has been observed to
crash the interpreter outright on Windows during rapid reload cycles; this is
an interpreter and dependency interaction, not a PriceLab defect, and the
pinned Docker base does not exhibit it. `.streamlit/config.toml` also turns
the watcher off outright, since a deployed container's code does not change
at runtime and has no need of it.
