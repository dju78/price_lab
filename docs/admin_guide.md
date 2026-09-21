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
python -m pip install -e ".[dev]"
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

`migrations/versions/` holds six revisions (users/sessions/audit/registry/
classification; registry reference periods; full COICOP tree; validation
overrides and column mappings; the quality-adjustment ledger; registry
headline and data vintage). Revisions are additive and idempotent where
they touch existing tables. `alembic upgrade head` after every deployment;
`alembic downgrade -1` reverses the last one.

## 8. Upgrading pyarrow on the development machine

If a future pyarrow wheel loads under the Application Control policy
(`python -c "import pyarrow.parquet"` succeeds), lift the `<25` ceiling in
`pyproject.toml` and delete the comment above it. Linux CI and the container
are unaffected either way.
