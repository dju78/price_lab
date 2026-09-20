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

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = get_settings().database_url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, connect_args=connect_args)
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
