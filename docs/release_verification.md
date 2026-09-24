# Release 1.0.0: what was verified, and what was not

## Test suite

The full suite (1013 tests) was run on the final tree on this machine
(Windows 11, Python 3.14): on SQLite with engine coverage, then in the
reproduced image layout (item 4 below), then on a local PostgreSQL 15
cluster, last. The tree was fingerprinted before, between and after the
runs, and committed unchanged as the release commit.

The results are in the message of the annotated tag (`git show v1.0.0`),
not in this file: writing them here would change the tree they were
measured on.

## Docker

**The image has never been built, and `docker compose up` has never run.**
This machine has no container engine: no Docker, no Podman, and no Windows
Subsystem for Linux (`wsl --status`: "not installed"). Installing one means
running `wsl --install`, which turns on Windows optional features, needs
administrator rights and restarts the machine, and then installing Docker
Desktop. Those are system changes to the development machine, and they were
not made for this release.

What could be checked without an engine was checked.

1. **The build context.** Every file in the repository was matched against
   `.dockerignore` the way Docker matches it: against the whole path from the
   context root and each parent directory, with `*` never crossing a `/`, and
   the last matching rule winning. This found a real fault. `__pycache__/`,
   `*.pyc` and the other rules matched only at the top level, so **194 stale
   bytecode files** from `pricelab/`, `pages/` and `tests/` would have entered
   the image. Every rule now applies at any depth (`**/`), and the Parquet
   store (`store/`) is excluded from the context. The negation
   `!supermarket_price_collection.xlsx` does re-include the bundled
   collection after `**/*.xlsx`; it is the only workbook in the context.
2. **The copies.** Each `COPY` line was applied, in order, from that context:
   - every source exists in the context;
   - no file under a copied path is excluded by `.dockerignore`;
   - `/app` receives 266 files, including `.streamlit/config.toml` and the
     bundled collection the hard-gate test needs;
   - no test reads a file the image lacks (`docs/`, `README.md` and the
     other excluded files are read by no test).
3. **The build order.** The Dockerfile ran `pip install -e ".[dev]"` with only
   `pyproject.toml` in place, before `pricelab/` was copied. An editable
   install of a project whose package directory does not exist yet maps no
   package, and the image would then have imported `pricelab` only because
   `/app` happens to be the working directory. The source is now copied
   before the install. **Not verified:** that step's behaviour, before or
   after the change. Testing it needs setuptools downloaded into a fresh
   environment, which was not done.
4. **The build's own checks, in the image's layout.** The reproduced `/app`
   (no `.git`, exactly the copied files) was used as the working directory
   and the Dockerfile's three `RUN` checks were run there. Imports were
   confirmed to resolve to the reproduced tree, not to the repository.

   | Step | Result |
   |---|---|
   | `ruff check .` | no issues |
   | `mypy` | no issues, 73 source files |
   | `python -m pytest tests/ -q` | in the tag message (see above) |

   The first run found a fault the repository's own runs had hidden:
   `test_seasonal_carried.py` rendered the Seasonality page and the evidence
   pack against the *default* database before setting up its own. In the
   working copy that default is a git-ignored `pricelab.db` left from
   development, which already had every table, so the test passed. In the
   image's clean `/app` there is no such file, and the test failed with "no
   such table: audit_events". It would have failed the same way in CI and on
   any clean checkout. The test now creates its own database first.

   The check for stray files then found a second, in the product itself.
   `run_pipeline` looks for a classification tree whenever a collection is
   weighted. With no database configured it opened the default SQLite URL,
   which *creates* an empty file, found no table and carried on. So using the
   library without a database left an empty `pricelab.db` behind, and the
   Docker build's test step left one in the image. It now checks that a SQLite
   file exists before opening it (`classification.parent_map_for`), and a test
   holds it. The final gate checks that no test leaves a database file in the
   image's working directory; its result is in the tag message.

   The same pass showed that `tests/test_release.py`, added in this release,
   read documents the image does not ship, so it would have failed the build.
   Those checks now skip there, and say why. The version check still runs.

5. **Compose.** `alembic upgrade head` in the compose command imports
   `pricelab` through `alembic.ini`'s `prepend_sys_path = .` and
   `migrations/env.py`'s own path insertion, so it does not depend on the
   editable install. The healthcheck calls `python -m pricelab.core.health
   --ready`, which the test suite exercises.

**What the reproduced layout does not check.** It runs the image's files in
the development environment, so it cannot find a dependency the code uses
but `pyproject.toml` does not declare. 1.0.0 had one: `pypdf`. The first CI
run on 1.0.0 (Python 3.12, Linux, a clean install of the declared
dependencies) found it. Installation, ruff and mypy passed, then test
collection failed on both backends. 1.0.1 declares it, and a test now fails
on any undeclared third-party import.

**Still unverified, precisely:**
- that `python:3.12-slim` builds the image: the `apt-get` step, the `pip`
  resolution on Linux for Python 3.12, and the editable install in item 3;
- the suite on Python 3.12 and on Linux. CI's first run, on 1.0.0,
  installed, linted and type-checked cleanly there, then failed at test
  collection on the undeclared `pypdf`. Whether 1.0.1's suite passes there
  is known only once CI runs on it;
- that the container starts, serves Streamlit on 8501 and passes its
  healthcheck;
- `docker compose up`: PostgreSQL health-gating the app, migration to head
  against PostgreSQL inside the network, the named volumes, and the `tests`
  profile creating `pricelab_test` and passing;
- the image's size and layer caching.

To close them, on any machine with Docker:

```bash
docker build -t pricelab:1.0.0 .
docker compose up -d
docker compose run --rm tests
```

The build itself runs ruff, mypy and the full suite, and fails on any of
them.

## X-13ARIMA-SEATS: a decision for the owner

**Position.** The seasonal module's X-13 path has **never run against the
real program**. X-13ARIMA-SEATS is not installed on the development machine,
so every seasonally adjusted series produced so far is STL's, and every
output says so. The X-13 branch has been tested only through a substituted
runner. The tests check that the program is found on `PATH` or through
`X13PATH`, that a demand for X-13 without it fails rather than substitute,
and that when it runs its result is named X-13. Nothing yet shows that
statsmodels' wrapper, called as `_run_x13` calls it, returns sensible factors
from the real binary on this platform's series.

**What installing it takes.**
- Download the X-13ARIMA-SEATS executable from the US Census Bureau (a
  Windows build, and a Linux build for the image) and put it on `PATH` or
  set `X13PATH`. In the image, that is a Dockerfile step fetching and
  checksum-verifying the Linux build.
- Add an integration test, run only where the binary exists, that adjusts a
  series with a published X-13 adjustment and compares the factors.
- Decide whether to extend `_run_x13`. It calls statsmodels'
  `x13_arima_analysis` with automatic outlier detection only
  (`trading=False` by default). So **installing the program alone would not
  add trading-day or Easter adjustment**; that needs the call extended and
  its own validation.

Estimated effort: about a day, including the validation.

**What it would add over STL.**
- regARIMA pre-adjustment with automatic detection of additive outliers,
  level shifts and temporary changes;
- forecast extension of the series before filtering, which reduces the
  revisions to the latest adjusted values;
- the X-11 filters and their standard diagnostics (the M and Q statistics,
  sliding spans, revision histories), which the STL path does not compute;
- the method most national statistical offices publish with, which matters
  to a reader comparing with an official adjusted series;
- with the call extended: trading-day and moving-holiday adjustment.

STL offers robustness, no external binary, and adjusted series that are
already labelled as STL everywhere. It gives no calendar adjustment and has
larger end-point revisions.

**The owner's decision, implemented.** STL remains the working method. The
X-13 branch is gated behind an administrator setting
(`PRICELAB_X13_ENABLED`, off by default):
- With the setting off, X-13 does not run, even where the binary is
  installed. The automatic choice uses STL, and the existing fallback label
  names the setting as the reason; a demand for X-13 alone is refused with
  the reason.
- With the setting on, every output names the engine as "an unvalidated
  path" until `seasonal.X13_VALIDATED` is set, in the commit that adds the
  integration test against a published official adjustment.

Limitation A13 records what validating it involves.

## Other verification in this release

- **Wiring audit**: `docs/wiring_audit.md`. Every public name in `core/`,
  `engine/`, `data/` and `reporting/` is reachable from a page or explained.
  Seven gaps were closed, each with a page-level test.
- **Documents cite what exists.** `tests/test_release.py` fails if the
  methodology statement leaves a methodology note uncited or links to one
  that does not exist; if the limitations register lacks a phase or a group;
  or if the package and project versions disagree.
