# PriceLab administrator guide

Installation, configuration, user management, backup and restore, probes
and logs. Everything here is environment-driven; nothing secret has a
default or lives in the repository.

## 1. Installation

### Local (development or a single-server deployment)

Python 3.12 or later, and `git`. Clone the repository, then, from the
repository root:

```bash
python -m venv .venv
source .venv/bin/activate                 # Windows (PowerShell): .venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock  # the locked versions CI tests
python -m pip install -e . --no-deps
alembic upgrade head                      # creates the database at PRICELAB_DATABASE_URL
python scripts/create_user.py --username admin --role administrator
streamlit run app.py
```

Every later command in this guide assumes the virtual environment is
activated (`alembic`, `python`, `streamlit` and the scripts all come from
it). If you would rather not activate, prefix each with the environment's
own interpreter (`.venv/bin/python -m ...`, or `.venv\Scripts\python -m ...`
on Windows); `python -m pip` is used above rather than a `pip` executable
for the same reason.

- `alembic upgrade head` creates `pricelab.db` at the repository root
  (the default `PRICELAB_DATABASE_URL`) and applies all six revisions.
- `create_user.py` prompts twice for a password of at least eight
  characters; it needs a terminal. For unattended provisioning pipe the
  password with `--password-stdin` (section 3).
- `streamlit run app.py` prints the URL to open (`http://localhost:8501`
  by default). The repository's `.streamlit/config.toml` runs the server
  headless, so it neither opens a browser nor stops at Streamlit's
  first-run email prompt.

On Windows, clone to a short path (`C:\pricelab`, not a deep Documents or
OneDrive folder): pyarrow's header tree pushes a long checkout path past
the 260-character limit and `pip install` fails with a "No such file or
directory" error naming a `pyarrow\include\...` file, unless long paths
are enabled system-wide. The `pyarrow` constraint is pinned below 25 (see
`pyproject.toml` for the reason: Windows Smart App Control refuses
25.0.1's `_fs.pyd`); a fresh install honours the pin.

`alembic upgrade head` is the supported way to create or upgrade the schema.
`app.py` also calls `create_all` at start so a development database works
with no step, but a deployed database should be migrated explicitly: the
readiness probe reports a database whose `alembic_version` is behind the
scripts as *not ready*.

### Container

```bash
docker compose up --build
```

Two services: `postgres` (PostgreSQL 15, its data on the
`pricelab-postgres` volume) and `pricelab`, which runs `alembic upgrade
head` and then the server, against PostgreSQL at
`postgresql+psycopg://pricelab:...@postgres:5432/pricelab`. Set
`PRICELAB_DB_PASSWORD` in the environment (or a `.env` file beside the
compose file) before the first start; the default is for a local trial
only. The image runs lint, type check and the full test suite during the
build and refuses to build if any fail. The Parquet store lives on the
`pricelab-data` volume (`/data/store`). The container's `HEALTHCHECK` is
the readiness probe (section 5). To create the first administrator inside
the container:

```bash
echo "$ADMIN_PASSWORD" | docker compose exec -T pricelab \
    python scripts/create_user.py --username admin --role administrator --password-stdin
```

To run the test suite against the PostgreSQL service:

```bash
docker compose run --rm tests
```

To use SQLite in the container instead, set `PRICELAB_DATABASE_URL` to
`sqlite:////data/pricelab.db` for the `pricelab` service.

The image has no `.git` directory, so pass the commit it is built from.
Every run registered from the image records it as its code version;
without it they record `unknown`, and their stamps say the code cannot be
identified:

```bash
PRICELAB_CODE_VERSION="$(git rev-parse HEAD)$(git diff --quiet HEAD || echo -dirty)" \
    docker compose build
```

### Streamlit Community Cloud: a demonstration only

Community Cloud is suitable for the **demonstration account only**, and
nothing else:
- **it is memory-limited.** An app shares a small, fixed allowance of memory
  and CPU (Streamlit publishes the current limits). Compiling a large
  collection, fitting hedonic models on a year of transactions, or running
  the forecasting and sensitivity engines can exceed it, and the app is then
  restarted, which brings the next point into play. The demonstration's
  bundled collection is small enough;
- **its storage is ephemeral.** The filesystem does not persist across
  restarts, so a SQLite database and the Parquet store on it are lost every
  time the instance sleeps, restarts or is redeployed;
- with them go the audit log, the run registry, every user account, every
  raw layer and every transformation log.

Community Cloud installs the app's dependencies from `requirements.txt` at
the repository root. It does not install from a setuptools `pyproject.toml`
the way it does from a Poetry one; that is why the deployment failed with
"Error installing requirements" until the file existed. `requirements.txt`
is generated, never edited by hand (section 10). It holds the runtime
dependencies only, at exactly the versions in `requirements.lock`, so the
demonstration runs what CI tested.

The platform presents those as governance guarantees, and on ephemeral
storage they silently reset. So an empty audit log there cannot tell a
visitor whether nothing happened or everything was lost. **Any real use
needs PostgreSQL** (`PRICELAB_DATABASE_URL`) **and persistent storage for
the Parquet layers** (`PRICELAB_STORE_DIR` on a volume that survives
restarts), as the container setup above provides.

For the demonstration, set two secrets in the app's settings (Community
Cloud exposes top-level secrets as environment variables):

```toml
PRICELAB_DEMO_USERNAME = "demo"
PRICELAB_DEMO_PASSWORD = "a password you publish with the link"
```

On start, `core/demo.py` then does the following:
- it creates **one viewer account** with those credentials, and registers
  and approves one run of the bundled collection, so the viewer has
  something to open on Reports and Audit;
- it creates only a viewer: no setting can make it create a compiler or an
  administrator;
- it **refuses to create anything when the database already holds any
  user**, so on a real deployment it is inert whatever the environment says.

While that account is signed in, every page shows a banner: this is a
demonstration, uploaded data is not retained, and the audit log and run
registry reset whenever the instance restarts. The sign-in screen shows it
too.

A viewer sees Reports and Audit only (the role model in section 3). A
demonstration of the analytical pages would need an analyst account, which
the seeding path deliberately cannot create. Publish the demonstration
credentials with the link; they open nothing but a read-only view of a
disposable instance.

Never set these two variables on a real deployment. The refusal protects
you if you do, but the variables have no purpose there.

## 2. Environment variables

All prefixed `PRICELAB_`; also read from a `.env` file in the working
directory. Defaults are development-safe.

| Variable | Default | Meaning |
|---|---|---|
| `ENVIRONMENT` | `development` | `production` makes the app more cautious (no stack traces) |
| `DATABASE_URL` | `sqlite:///<repo>/pricelab.db` | SQLAlchemy URL. PostgreSQL: `postgresql+psycopg://user:password@host:5432/dbname` (the `psycopg` driver is installed with the package); every query goes through SQLAlchemy, and the suite is run against both backends |
| `STORE_DIR` | `<repo>/store` | Parquet store: `raw/` (immutable, named by content hash, with `.vintage.json` receipts), `cleaned/` (with `.log.json` transformation logs) |
| `UPLOAD_MAX_MB` | `50` | Refused before any bytes are read |
| `UPLOAD_RATE_LIMIT_PER_MINUTE` | `10` | Per signed-in user; the next upload is refused with the wait stated |
| `SESSION_TIMEOUT_MINUTES` | `60` | Rolling idle timeout; an expired session is deleted and lands on sign-in |
| `SUPPRESSION_MIN_COUNT` | `3` | Cells built from fewer matched quotes are suppressed on every published table |
| `CACHE_MAX_ENTRIES` | `8` | Analyses held in the in-process result cache |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_TIMEOUT_SECONDS` | `5` / `5` / `30` | Connection pool (server databases); a request waits at most the timeout for a connection |
| `DB_STATEMENT_TIMEOUT_MS` | `30000` | PostgreSQL `statement_timeout`; for SQLite the busy timeout and a per-statement abort |
| `LOG_JSON` / `LOG_LEVEL` | `true` / `INFO` | One JSON object per log line, with the run's correlation id on every line |
| `RELEASE_ORGANISATION`, `RELEASE_CONTACT_NAME`, `RELEASE_CONTACT_EMAIL`, `RELEASE_CONTACT_PHONE` | `PriceLab`, `Statistical enquiries`, `not configured`, empty | The bulletin's title and contact block |
| `X13_ENABLED` | `false` | Allows X-13ARIMA-SEATS to run at all (section 8). Off: STL, labelled as such, even where the binary is installed |
| `CODE_VERSION` | unset | The commit an image was built from (`<hash>` or `<hash>-dirty`); read only where there is no git checkout. Recorded with every registered run |
| `DEMO_USERNAME` / `DEMO_PASSWORD` | unset | Public demonstration only: seed one viewer into an empty database (section 1, Community Cloud) |
| `PRICELAB_TEST_DATABASE_URL` (tests only) | unset | Point the test suite at a PostgreSQL database; each test drops and recreates its `public` schema, so never a database with anything in it |

## 3. User management

There is no self-registration and no in-app user page.

```bash
python scripts/create_user.py --username jane --role compiler
```

Roles: `administrator`, `compiler`, `analyst`, `viewer` (see the user
guide). The password is prompted for twice, never passed on the command
line, and stored as an Argon2 hash; eight characters minimum. The prompt
needs a terminal -- piping a password into it hangs on Windows, where
`getpass` reads the console directly. For unattended provisioning:

```bash
echo "$PASSWORD" | python scripts/create_user.py --username jane --role compiler --password-stdin
```

A username that already exists is refused. A role change or revocation takes effect on the
user's next request, because the role is re-read from the database on every
request rather than carried in the session token. To revoke access, delete
the user's row (`users` table) or change the role to `viewer`.

## 4. Backup and restore

What cannot be regenerated: the database (users, sessions, the hash-chained
audit log, the run registry with every registered input and headline, the
quality-adjustment ledger, validation overrides, column mappings, the
classification tree) and the Parquet store (raw layers, cleaned layers,
receipts, transformation logs). A backup is one directory with a manifest
carrying a SHA-256 per file.

```bash
python scripts/backup.py --to backups           # creates backups/pricelab-<UTC timestamp>Z/
```

`--to` is any directory you can write to; the timestamped backup is created
inside it. A fresh installation with no uploads backs up one file (the
database); the store is added as uploads arrive.

The database snapshot uses SQLite's online backup API, so it is consistent
even while the application is running; the store is copied file for file.
For a PostgreSQL deployment `backup.py` refuses with a message saying so
(it will not pretend a file copy is a database backup): use `pg_dump` for
the database and copy `PRICELAB_STORE_DIR` alongside it, and restore with
`pg_restore` plus a copy of the store back into place.

To restore, **stop everything that has the database open** -- the
application, and also a probe server started with `--serve` (section 5) or
any shell holding the file; on Windows an open SQLite file cannot be
replaced and the script says so -- then:

```bash
python scripts/restore.py backups/pricelab-20260921T120000Z --overwrite
```

Every file is checked against the manifest before it is trusted; a damaged
backup is refused. Without `--overwrite` an existing database or a
non-empty store is refused, with the reason printed. Start the application
again afterwards.

**Tested** means exactly this: `tests/test_backup.py` registers and
approves a run, backs up, deletes the database and the store, restores, and
reproduces the run from the restored registry, asserting the index series
equals the pre-backup one value for value and the restored raw layer still
replays through its transformation log. The scripts themselves are run in
that test. Run the procedure yourself after installation and periodically;
a backup that has never been restored is a hope.

## 5. Health and readiness probes

```bash
python -m pricelab.core.health --live      # exit 0 if the process is up; never touches the database
python -m pricelab.core.health --ready     # exit 0 if the database answers and is at the migration head and the store is writable
python -m pricelab.core.health --serve 8600   # HTTP: /health/live, /health/ready (503 when not ready)
```

`--serve` runs in the foreground until stopped (Ctrl-C) and keeps a
connection to the database while it runs; stop it before a restore.

The Docker `HEALTHCHECK` runs `--ready`. Streamlit's own `/_stcore/health`
only says the web process is up.

## 6. Logs

With `LOG_JSON=true` every line is a JSON object: `ts`, `level`, `logger`,
`message`, `correlation_id`, plus the fields the emitting code attached
(rows, elapsed milliseconds, adjustment effects). Every line emitted during
one pipeline run carries that run's `correlation_id`; the same id is written
into the run's `CALCULATION_RUN` audit event and into the result, so a
figure on screen, its audit entry and its log lines can be joined on one
field. Send stderr to your aggregator.

## 7. Migrations

`migrations/versions/` holds seven revisions (users/sessions/audit/registry/
classification; registry reference periods; full COICOP tree; validation
overrides and column mappings; the quality-adjustment ledger; registry
headline and data vintage; the outlier review queue). Revisions are additive and idempotent where
they touch existing tables. `alembic upgrade head` after every deployment;
`alembic downgrade -1` reverses the last one.

## 8. X-13ARIMA-SEATS

STL is the working method. X-13ARIMA-SEATS runs **only when an administrator
sets `PRICELAB_X13_ENABLED=true`**. The binary merely being installed
changes nothing: with the setting off, the automatic choice uses STL and
every output says why, while a request for X-13 alone is refused with the
reason.

PriceLab's X-13 path has never run against the real program. Its tests
substitute the runner. Until an integration test adjusts a series that has
a published official X-13 adjustment and matches it, every output from the
X-13 path is labelled "an unvalidated path". Validating it means:
- installing the binary where the suite runs;
- adding that test, with the official series and its published factors
  recorded as a fixture;
- setting `engine.seasonal.X13_VALIDATED` in the same commit.

See `docs/release_verification.md` for what X-13 would add and what it
would not. It adds no trading-day or Easter adjustment unless the call to
it is extended.

To install and enable it, download the X-13ARIMA-SEATS binary from the US Census Bureau, put the
executable (`x13as`, or `x13as.exe` on Windows) somewhere stable, and either
put its directory on `PATH` or set `X13PATH` to it. `PRICELAB_` is not a
prefix here: statsmodels reads `X13PATH` and `X12PATH` directly, and the
application looks in the same two places.

Verify from a shell in the deployment environment:

```bash
python -c "from pricelab.engine.seasonal import x13_available; print(x13_available())"
```

It prints `(True, "<directory>", "...found...")` when the binary is usable.
Then set `PRICELAB_X13_ENABLED=true` and restart. Until both hold, the
Seasonality page says which is missing at the top of its adjustment
section, and every adjusted series is labelled as STL.

## 9. Upgrading pyarrow on the development machine

If a future pyarrow wheel loads under the Application Control policy
(`python -c "import pyarrow.parquet"` succeeds), lift the `<25` ceiling in
`pyproject.toml` and delete the comment above it. Linux CI and the container
are unaffected either way.

## 10. Dependencies: the lock

`requirements.lock` pins every package the platform, its tests and its tools
install, at one version each. It is compiled by uv from `pyproject.toml`,
covering all extras, and resolved *universally*: one file that gives the
same versions on Windows and Linux and on Python 3.12 and later (3.11 differs
only for numpy and scipy). Local installs, CI (`.github/workflows/tests.yml`)
and the Docker image all install from it and never resolve afresh, so a new
upstream release cannot change a result between two runs of the same
commit. `tests/test_release.py` fails if a declared dependency is missing
from the lock or locked at a version its specifier rejects, if CI or the
Dockerfile installs anything but the lock, or if Streamlit loses its upper
bound.

Streamlit is also pinned to one minor series in `pyproject.toml`
(`>=1.64,<1.65`). The pages and their tests use Streamlit APIs that change
between minor releases (1.64 alone failed 25 tests), so moving it is a
decision, not a resolution. A deployment that installs from `pyproject.toml`
rather than the lock, such as Streamlit Community Cloud, gets the right
series too.

**Regenerating it.** From the repository root, with uv installed
(`python -m pip install uv`):

```bash
uv pip compile pyproject.toml --all-extras --universal --python-version 3.11 \
    --upgrade --no-emit-package pricelab --output-file requirements.lock
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps
```

**The runtime `requirements.txt`** is generated from the lock, never
edited: `pyproject.toml` stays the source of truth, `requirements.lock` pins
it, and `requirements.txt` is the lock's runtime subset (no pytest, mypy,
ruff, hypothesis or other development tool), which Streamlit Community Cloud
installs. Regenerate it whenever the lock changes:

```bash
uv pip compile pyproject.toml --universal --python-version 3.11 \
    --constraint requirements.lock --no-emit-package pricelab \
    --custom-compile-command "make requirements (generated from pyproject.toml, pinned to requirements.lock; never edit by hand)" \
    --output-file requirements.txt
```

`make requirements` runs it, and `make lock` runs it after recompiling the
lock, so the two move together. `tests/test_release.py` fails if any pin in
`requirements.txt` differs from the lock, if a runtime dependency is missing
from it, or if a development tool is in it. The two cannot drift unnoticed.

`make lock` runs the first command. Without `--upgrade`, uv keeps every
existing pin and resolves only what changed in `pyproject.toml`; use that
after adding or tightening a dependency. With `--upgrade`, everything moves
to the newest version allowed. Commit the new lock only after the full suite
passes on it, on both backends. To move Streamlit to its next minor series,
change its range in `pyproject.toml`, recompile, fix what breaks, and commit
all three together.

**Finding a breaking release on purpose.** `.github/workflows/lock-refresh.yml`
runs every Monday at 06:00 UTC, and on demand (Actions, *Lock refresh*,
*Run workflow*). It has two jobs, and neither changes the repository:

- `refresh` recompiles the lock with `--upgrade`, regenerates
  `requirements.txt` from it, and runs ruff, mypy and the suite (SQLite). The
  run summary lists every version that moved in each file, and both files
  are attached as an artifact. When it is green and you want the upgrade,
  download `requirements-lock-refreshed`, replace `requirements.lock` and
  `requirements.txt` with its two files, and commit them together. The Tests workflow then checks it
  like any other change.
- `streamlit-latest` installs the committed lock with only Streamlit moved to
  its newest release, past the pin, and runs the suite. It turns red when a
  new Streamlit minor would break the pages. That is the signal to plan the
  move, before anyone installs it by accident.

A red refresh job does not affect the committed lock or the Tests workflow.
It says which upstream release will need work.
