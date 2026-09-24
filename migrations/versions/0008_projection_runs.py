"""Add projection_runs (core.registry.ProjectionRunORM), Phase 9b's record of
a forecast or scenario made from a registered run: its specification, the
driver series a regression used, and digests of its backtest errors and
path, so a backtest can be reproduced from the registry and checked.

Created only where missing, in the 0002/0006/0007 style.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None

TABLE = "projection_runs"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE in inspector.get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("projection_id", sa.String(length=32), nullable=False, unique=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("series", sa.String(length=255), nullable=False),
        sa.Column("spec_json", sa.String(), nullable=False),
        sa.Column("driver_json", sa.String(), nullable=True),
        sa.Column("backtest_digest", sa.String(length=64), nullable=False),
        sa.Column("path_digest", sa.String(length=64), nullable=False),
        sa.Column("code_version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
    )
    op.create_index("ix_projection_runs_run_id", TABLE, ["run_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return
    op.drop_index("ix_projection_runs_run_id", table_name=TABLE)
    op.drop_table(TABLE)
