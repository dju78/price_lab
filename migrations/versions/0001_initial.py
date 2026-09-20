"""Initial schema: users, sessions, audit events, index runs (vintages are
runs linked by supersedes_run_id/vintage rather than a separate table -- a
vintage IS a registered run, not a distinct kind of record), and the
classification tree, seeded with the thirteen COICOP 2018 divisions.

index_runs' price_reference_period / weight_reference_period /
index_reference_period columns were added to this same migration rather
than a second one: this revision has no production data behind it yet, so
there is nothing a later migration would need to ALTER around.

Revision ID: 0001
Revises:
Create Date: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(length=255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token", sa.String(length=64), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("last_seen_at", sa.String(length=32), nullable=False),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("params_json", sa.String(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.String(length=32), nullable=False),
    )

    op.create_table(
        "index_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=32), nullable=False, unique=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("config_json", sa.String(), nullable=False),
        sa.Column("code_version", sa.String(length=64), nullable=False),
        sa.Column("environment_fingerprint", sa.String(), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("vintage", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("correction_reason", sa.String(), nullable=True),
        sa.Column("supersedes_run_id", sa.String(length=32), nullable=True),
        sa.Column("input_parquet", sa.LargeBinary(), nullable=False),
        sa.Column("price_reference_period", sa.String(length=32), nullable=True),
        sa.Column("weight_reference_period", sa.String(length=32), nullable=True),
        sa.Column("index_reference_period", sa.String(length=32), nullable=True),
    )

    classification_nodes = op.create_table(
        "classification_nodes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("scheme", sa.String(length=32), nullable=False, server_default="COICOP2018"),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("parent_code", sa.String(length=32), nullable=True),
    )

    # The thirteen COICOP 2018 divisions (see data/classification.py for why
    # only the top level is seeded here rather than the full tree).
    divisions = [
        ("01", "Food and non-alcoholic beverages"),
        ("02", "Alcoholic beverages, tobacco and narcotics"),
        ("03", "Clothing and footwear"),
        ("04", "Housing, water, electricity, gas and other fuels"),
        ("05", "Furnishings, household equipment and routine household maintenance"),
        ("06", "Health"),
        ("07", "Transport"),
        ("08", "Information and communication"),
        ("09", "Recreation, sport and culture"),
        ("10", "Education services"),
        ("11", "Restaurants and accommodation services"),
        ("12", "Insurance and financial services"),
        ("13", "Personal care, social protection and miscellaneous goods and services"),
    ]
    op.bulk_insert(
        classification_nodes,
        [
            {"scheme": "COICOP2018", "code": code, "label": label, "level": 0, "parent_code": None}
            for code, label in divisions
        ],
    )


def downgrade() -> None:
    op.drop_table("classification_nodes")
    op.drop_table("index_runs")
    op.drop_table("audit_events")
    op.drop_table("sessions")
    op.drop_table("users")
