"""Add quality_adjustments (core.ledger.QualityAdjustmentORM), Phase 4's
approval record for replacement valuations.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "quality_adjustments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=255), nullable=False),
        sa.Column("old_item", sa.String(length=255), nullable=False),
        sa.Column("new_item", sa.String(length=255), nullable=False),
        sa.Column("period", sa.String(length=32), nullable=False),
        sa.Column("method", sa.String(length=64), nullable=False),
        sa.Column("quality_ratio", sa.Float(), nullable=False),
        sa.Column("parameters_json", sa.String(), nullable=False),
        sa.Column("justification", sa.String(), nullable=False),
        sa.Column("approved_by", sa.String(length=255), nullable=False),
        sa.Column("approved_at", sa.String(length=32), nullable=False),
        sa.Column("withdrawn_at", sa.String(length=32), nullable=True),
        sa.Column("withdrawn_by", sa.String(length=255), nullable=True),
        sa.Column("withdrawal_reason", sa.String(), nullable=True),
    )
    op.create_index("ix_quality_adjustments_content_hash", "quality_adjustments",
                    ["content_hash"])


def downgrade() -> None:
    op.drop_index("ix_quality_adjustments_content_hash", table_name="quality_adjustments")
    op.drop_table("quality_adjustments")
