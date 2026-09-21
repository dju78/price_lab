"""Alembic environment.

Reads the database URL from `pricelab.core.config.Settings` (which is itself
sourced from `PRICELAB_DATABASE_URL`) rather than only from alembic.ini, so
one environment variable controls migrations, the application and the test
suite alike. Imports every module that defines a table before touching
`Base.metadata`, since a module whose ORM class was never imported leaves no
trace on the metadata for autogenerate to see.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pricelab.core import (  # noqa: F401,E402  side effect: register tables
    audit,
    ledger,
    registry,
    security,
)
from pricelab.core.config import get_settings  # noqa: E402
from pricelab.core.db import Base  # noqa: E402
from pricelab.data import (  # noqa: F401,E402  side effect: register tables
    classification,
    mapping,
    validation,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True,
        dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
