"""Classification trees: COICOP today, CPA/NACE/HS/custom trees as the
platform grows into producer, trade and construction indices (see
docs/backlog.md; out of scope for the minimum credible release).

Only the thirteen COICOP 2018 divisions -- the top level of the tree -- are
seeded as reference data (migrations/versions). Deeper groups and classes
run into the hundreds of codes; seeding them accurately would mean
transcribing the full UN COICOP 2018 structure from its source publication
rather than from anything already verified in this codebase, and getting a
classification code wrong is worse than not having it yet. The thirteen
divisions are stable, well-known, and safe to seed directly; deeper levels
are better added once there is a verified source to load them from (a
Phase 2 connector concern) than fabricated now.
"""

from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from ..core.db import Base

#: The thirteen COICOP 2018 divisions, per the 2018 revision of the UN
#: Classification of Individual Consumption According to Purpose (which
#: restructured the previous twelve-division 1999 version, folding
#: communication into division 08 and adding division 12).
COICOP_2018_DIVISIONS: tuple[tuple[str, str], ...] = (
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
)


class ClassificationNodeORM(Base):
    __tablename__ = "classification_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scheme: Mapped[str] = mapped_column(String(32), nullable=False, default="COICOP2018")
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_code: Mapped[str | None] = mapped_column(String(32), nullable=True)


def seed_coicop_divisions(session: Session) -> None:
    """Insert the thirteen COICOP 2018 divisions if they are not already
    present. Safe to call more than once."""
    existing = {
        row.code
        for row in session.query(ClassificationNodeORM).filter_by(scheme="COICOP2018", level=0)
    }
    for code, label in COICOP_2018_DIVISIONS:
        if code in existing:
            continue
        session.add(ClassificationNodeORM(
            scheme="COICOP2018", code=code, label=label, level=0, parent_code=None))
    session.flush()
