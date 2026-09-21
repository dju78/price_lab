"""Add the headline figure and the data vintage to index_runs, so a
bulletin can read the published number from the registry rather than
recompute it, and so every export can name the vintage of the data it
came from (Phase 10).

Additive and idempotent in the 0002 style: columns are added only where
missing, all nullable, because runs registered before this revision have
no headline recorded and the honest record of that is NULL.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None

_NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine[object]], ...] = (
    ("headline_series", sa.String(length=255)),
    ("headline_period", sa.String(length=32)),
    ("headline_value", sa.Float()),
    ("headline_reference_period", sa.String(length=32)),
    ("data_source", sa.String(length=255)),
    ("data_received_at", sa.String(length=32)),
)


def _existing_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {c["name"] for c in inspector.get_columns(table_name)}


def upgrade() -> None:
    existing = _existing_columns("index_runs")
    with op.batch_alter_table("index_runs") as batch_op:
        for name, kind in _NEW_COLUMNS:
            if name not in existing:
                batch_op.add_column(sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    existing = _existing_columns("index_runs")
    with op.batch_alter_table("index_runs") as batch_op:
        for name, _kind in _NEW_COLUMNS:
            if name in existing:
                batch_op.drop_column(name)
