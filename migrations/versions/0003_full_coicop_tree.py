"""Load the complete COICOP 2018 tree (871 codes, divisions through
sub-classes) from the vendored, source-derived reference CSV, superseding
0001's thirteen hand-typed divisions.

Idempotent by design (delegates to
`pricelab.data.classification.seed_coicop_2018`, which only inserts a
`(scheme, code)` pair not already present): running this against a
database that already has the thirteen divisions from 0001 adds the other
858 codes (the two additional divisions covering NPISH and government
consumption, all groups, classes and sub-classes) without touching or
duplicating what is already there.

See `pricelab/data/classification.py` for the source citation (UN
Statistics Division, downloaded 2026-09-20) and why CPA/NACE/HS are not
seeded here.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-20
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pricelab.data.classification import seed_coicop_2018  # noqa: E402

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy.orm import Session

    with Session(bind=bind) as session:
        seed_coicop_2018(session)
        session.commit()


def downgrade() -> None:
    # Deliberately not reversed: downgrading would need to distinguish
    # "seeded by 0001" from "seeded by 0003" for the same scheme, and
    # deleting reference data that other tables may have started
    # referencing (a run's category mapped to a COICOP class, say) is a
    # data-loss operation this migration chain does not take lightly.
    # Downgrading past this revision is unsupported; restore from a backup
    # taken before upgrading if that is genuinely needed.
    pass
