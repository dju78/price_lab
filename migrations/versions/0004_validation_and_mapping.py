"""Add validation_overrides (data.validation.ValidationOverrideORM) and
column_mappings (data.mapping.ColumnMappingORM), Phase 2's two new tables.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "validation_overrides",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("dimension", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
    )

    op.create_table(
        "column_mappings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("file_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("schema_json", sa.String(), nullable=False),
        sa.Column("confirmed_by", sa.String(length=255), nullable=False),
        sa.Column("confirmed_at", sa.String(length=32), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("column_mappings")
    op.drop_table("validation_overrides")
