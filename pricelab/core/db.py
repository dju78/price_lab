"""SQLAlchemy engine, session factory and declarative base.

One file so every other core/ module shares the same engine and the same
declarative base, which is what lets Alembic generate a single, coherent
migration covering users, sessions, audit events, index runs, vintages and
the classification tree (see migrations/). Swapping SQLite for PostgreSQL in
production is a change to `Settings.database_url` and nothing else: every
query in core/ goes through SQLAlchemy Core/ORM rather than SQLite-specific
SQL.

The engine and session factory are built lazily and cached at module level,
not constructed once at import time, so a test can point `Settings` at a
throwaway database (typically `sqlite:///:memory:` or a temp file) and call
`reset_db_state()` to make that take effect, without every other test that
already imported this module being stuck talking to the previous database.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _make_engine(url: str) -> Engine:
    """Connection pooling and query timeouts, per backend.

    SQLite: `timeout` is the busy timeout (how long a writer waits for a
    lock before failing rather than hanging); a progress handler aborts
    any single statement that runs past `db_statement_timeout_ms`, with
    the deadline armed per statement by the cursor-execute events below.
    PostgreSQL and other server databases: a real pool (`pool_size`,
    `max_overflow`, `pool_timeout`), `pool_pre_ping` so a connection the
    server dropped is replaced rather than handed out, and the server's
    own `statement_timeout`.
    """
    settings = get_settings()
    timeout_s = settings.db_statement_timeout_ms / 1000.0
    if url.startswith("sqlite"):
        engine = create_engine(
            url, connect_args={"check_same_thread": False, "timeout": timeout_s},
            pool_pre_ping=True)

        @event.listens_for(engine, "connect")
        def _sqlite_connect(dbapi_conn: Any, record: Any) -> None:
            dbapi_conn.execute(f"PRAGMA busy_timeout={settings.db_statement_timeout_ms}")
            state: dict[str, float | None] = {"deadline": None}
            record.info["pricelab_deadline"] = state

            def _abort_if_late() -> int:
                deadline = state["deadline"]
                return 1 if deadline is not None and time.monotonic() > deadline else 0

            dbapi_conn.set_progress_handler(_abort_if_late, 1000)

        @event.listens_for(engine, "before_cursor_execute")
        def _arm(conn: Any, cursor: Any, statement: Any, parameters: Any, context: Any,
                 executemany: Any) -> None:
            state = conn.connection.info.get("pricelab_deadline")
            if state is not None:
                state["deadline"] = time.monotonic() + timeout_s

        @event.listens_for(engine, "after_cursor_execute")
        def _disarm(conn: Any, cursor: Any, statement: Any, parameters: Any, context: Any,
                    executemany: Any) -> None:
            state = conn.connection.info.get("pricelab_deadline")
            if state is not None:
                state["deadline"] = None

        return engine

    connect_args: dict[str, Any] = {}
    if url.startswith("postgresql"):
        connect_args["options"] = f"-c statement_timeout={settings.db_statement_timeout_ms}"
    return create_engine(
        url, connect_args=connect_args, pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow, pool_timeout=settings.db_pool_timeout_seconds,
        pool_pre_ping=True)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = _make_engine(get_settings().database_url)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


def reset_db_state() -> None:
    """Test-only: drop the cached engine and session factory so a newly
    configured `Settings().database_url` takes effect on next use, instead
    of every caller remaining bound to whichever database was current the
    first time this module was imported."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def init_db() -> None:
    """Create every table that does not already exist.

    Alembic (migrations/) owns schema evolution for a deployed database;
    this is the fast path for local development and for tests, where
    running an Alembic migration against a throwaway SQLite file is pure
    overhead for no benefit.
    """
    Base.metadata.create_all(bind=get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    """One short-lived session for one unit of work: committed on success,
    rolled back on error, always closed. Every core/ function that touches
    the database opens one of these rather than holding a session open
    across a Streamlit rerun."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
