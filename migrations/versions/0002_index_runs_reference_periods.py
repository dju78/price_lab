"""Add price_reference_period / weight_reference_period /
index_reference_period to index_runs, idempotently.

0001 was edited in place to add these same three columns, on the
reasoning that no production data existed yet for this project. That
reasoning is about this project's own history; it is not a guarantee
about every database anyone has ever pointed `alembic upgrade head` at
during development of this feature. A database that already applied the
original, pre-edit 0001 has `alembic_version` recording "0001" while its
`index_runs` table lacks these columns -- and because Alembic tracks
revisions by ID, not by content, it will never re-run 0001 to notice.
Left alone, the first query to touch one of these columns would fail with
a bare `OperationalError` naming a column the caller has no reason to
suspect is missing.

This migration is unconditionally safe to run regardless of which shape of
0001 a given database applied: it inspects the live table first and only
adds a column that is not already there, so a database created fresh from
the edited 0001 (where all three already exist) runs this as a no-op.
`op.batch_alter_table` is used rather than a bare `op.add_column` because
older SQLite cannot ALTER a table outside batch mode; batch mode is a
no-op wrapper on backends (PostgreSQL included) that support it directly.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None

_NEW_COLUMNS = ("price_reference_period", "weight_reference_period", "index_reference_period")


def _existing_columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {c["name"] for c in inspector.get_columns(table_name)}


def upgrade() -> None:
    existing = _existing_columns("index_runs")
    with op.batch_alter_table("index_runs") as batch_op:
        for name in _NEW_COLUMNS:
            if name not in existing:
                batch_op.add_column(sa.Column(name, sa.String(length=32), nullable=True))


def downgrade() -> None:
    existing = _existing_columns("index_runs")
    with op.batch_alter_table("index_runs") as batch_op:
        for name in _NEW_COLUMNS:
            if name in existing:
                batch_op.drop_column(name)
