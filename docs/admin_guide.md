# PriceLab administrator guide

Installation, configuration, user management, backup and restore, probes
and logs. Everything here is environment-driven; nothing secret has a
default or lives in the repository.

## 1. Installation

### Local (development or a single-server deployment)

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"        # Windows: .venv\Scripts\pip install -e ".[dev]"
alembic upgrade head                      # creates the database at PRICELAB_DATABASE_URL
python scripts/create_user.py --username admin --role administrator
streamlit run app.py
```

Python 3.12 or later. On the development machine the `pyarrow` constraint is
pinned below 25 (see `pyproject.toml` for the reason: Windows Smart App
Control refuses 25.0.1's `_fs.pyd`); a fresh install honours the pin.

`alembic upgrade head` is the supported way to create or upgrade the schema.
`app.py` also calls `create_all` at start so a development database works
with no step, but a deployed database should be migrated explicitly: the
readiness probe reports a database whose `alembic_version` is behind the
scripts as *not ready*.

### Container

```bash
docker compose up --build
```

The image runs lint, type check and the full test suite during the build
and refuses to build if any fail. The database and the Parquet store live
on the `pricelab-data` volume (`/data/pricelab.db`, `/data/store`), so they
survive `docker compose down`. The container's `HEALTHCHECK` is the
readiness probe (section 5).

## 2. Environment variables

All prefixed `PRICELAB_`; also read from a `.env` file in the working
directory. Defaults are development-safe.

| Variable | Default | Meaning |
|---|---|---|
| `ENVIRONMENT` | `development` | `production` makes the app more cautious (no stack traces) |
| `DATABASE_URL` | `sqlite:///<repo>/pricelab.db` | SQLAlchemy URL. PostgreSQL works (`postgresql://…`); every query goes through SQLAlchemy |
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

## 3. User management

There is no self-registration and no in-app user page.

```bash
python scripts/create_user.py --username jane --role compiler
```

Roles: `administrator`, `compiler`, `analyst`, `viewer` (see the user
guide). The password is prompted for, never passed on the command line, and
stored as an Argon2 hash. A role change or revocation takes effect on the
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
python scripts/backup.py --to /backups          # creates /backups/pricelab-<UTC timestamp>Z/
```

The database snapshot uses SQLite's online backup API, so it is consistent
even while the application is running; the store is copied file for file.
For PostgreSQL, use `pg_dump` for the database and `backup.py` still copies
the store (it says so rather than pretending a file copy is a backup).

To restore, **stop the application first** (an open database file cannot be
replaced), then:

```bash
python scripts/restore.py /backups/pricelab-20260921T120000Z --overwrite
```

Every file is checked against the manifest before it is trusted; a damaged
backup is refused. Without `--overwrite` a non-empty target is refused.

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
