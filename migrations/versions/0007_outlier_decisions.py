"""Add outlier_decisions (core.ledger.OutlierDecisionORM), Phase 6's review
queue record: who accepted, rejected or annotated which flagged quote, and
on what grounds.

Created only where missing, in the 0002/0006 style, so a database whose
tables were first created by `db.init_db()` (which creates whatever the
models declare) can still be stamped and migrated without colliding.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None

TABLE = "outlier_decisions"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE in inspector.get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("period", sa.String(length=32), nullable=False),
        sa.Column("item_id", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("method", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("statistic", sa.Float(), nullable=False, server_default="0"),
        # The reason is NOT NULL at the column level as well as on the
        # model. A decision with no stated grounds is the one thing this
        # table exists to make impossible, and making it impossible in the
        # schema means it stays impossible for a caller that bypasses the
        # model.
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("analyst", sa.String(length=255), nullable=False),
        sa.Column("decided_at", sa.String(length=32), nullable=False),
        sa.Column("withdrawn_at", sa.String(length=32), nullable=True),
        sa.Column("withdrawn_by", sa.String(length=255), nullable=True),
        sa.Column("withdrawal_reason", sa.String(), nullable=True),
    )
    op.create_index("ix_outlier_decisions_content_hash", TABLE, ["content_hash"])
    # One live decision per quote is enforced in `core.ledger` by withdrawing
    # the previous one rather than by a unique constraint here: a withdrawn
    # row stays, so (content_hash, period, item_id) is legitimately repeated
    # across the history of a quote that was decided twice.
    op.create_index("ix_outlier_decisions_quote", TABLE, ["content_hash", "period", "item_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return
    op.drop_index("ix_outlier_decisions_quote", table_name=TABLE)
    op.drop_index("ix_outlier_decisions_content_hash", table_name=TABLE)
    op.drop_table(TABLE)
